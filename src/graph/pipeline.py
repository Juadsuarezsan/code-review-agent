"""The review graph: parse -> context -> five parallel analyzers -> synthesize -> prioritize.

::

    parse_diff -> build_context -+-> detect_bugs --------+
                                 +-> check_style --------+
                                 +-> scan_security ------+-> synthesize_comments -> prioritize
                                 +-> review_performance -+
                                 +-> analyze_test_gaps --+

Every node logs its input/output sizes and timing through
:func:`~src.observability.node_span`. LLM-backed nodes degrade to their
deterministic part when no API key is configured, so the same graph serves the
"linter-only" baseline and the full pipeline.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, cast

import anthropic
from langgraph.graph import END, StateGraph
from loguru import logger

from src.analyzers.bug_detector import ClaudeBugDetector, static_scan
from src.analyzers.performance import review_performance
from src.analyzers.priority import prioritize
from src.analyzers.security_scanner import SecurityScanner, get_security_scanner, scan_security
from src.analyzers.style_checker import RuffLinter, StyleLinter, check_style
from src.analyzers.synthesizer import synthesize_deterministic, synthesize_with_claude
from src.analyzers.test_gap import detect_test_gaps
from src.api.schemas import ReviewComment, ReviewMode
from src.config import Settings
from src.context.builder import AstParser, FileContext, build_contexts, get_parser
from src.graph.state import ReviewState
from src.llm.client import ClaudeClient, LLMOutputError
from src.observability import RequestMetrics, node_span, trace_id_var
from src.parser.diff_parser import FileDiff, parse_unified_diff

NODE_NAMES = (
    "parse_diff",
    "build_context",
    "detect_bugs",
    "check_style",
    "scan_security",
    "review_performance",
    "analyze_test_gaps",
    "synthesize_comments",
    "prioritize",
)
ANALYZER_NODES = (
    "detect_bugs",
    "check_style",
    "scan_security",
    "review_performance",
    "analyze_test_gaps",
)


@dataclass
class ReviewResult:
    """Everything the API or CLI needs after a graph run."""

    comments: list[ReviewComment]
    files: list[FileDiff]
    contexts: list[FileContext]
    metrics: RequestMetrics
    mode: ReviewMode
    summary: str
    errors: list[str] = field(default_factory=list)

    @property
    def n_lines_added(self) -> int:
        """Total added lines across files."""
        return sum(f.n_added for f in self.files)

    @property
    def n_lines_removed(self) -> int:
        """Total removed lines across files."""
        return sum(f.n_removed for f in self.files)


def summarize(comments: list[ReviewComment], files: list[FileDiff]) -> str:
    """One-line human summary used as the review body."""
    if not comments:
        return f"Reviewed {len(files)} file(s); no issues flagged."
    n_err = sum(1 for c in comments if c.severity == "error")
    n_warn = sum(1 for c in comments if c.severity == "warning")
    n_info = len(comments) - n_err - n_warn
    return (
        f"{len(comments)} comment(s) across {len(files)} file(s): "
        f"{n_err} error, {n_warn} warning, {n_info} info."
    )


class ReviewPipeline:
    """Build and run the LangGraph review graph with injected dependencies.

    Args:
        settings: Application settings (limits, model, semgrep path).
        client: Claude client; ``None`` builds one from ``settings`` (disabled without key).
        linter: Style linter (defaults to :class:`RuffLinter`).
        scanner: Security scanner (defaults to semgrep if configured, else patterns).
        parser: AST parser for the context builder.
        use_llm: Force deterministic mode even when a key is configured.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: ClaudeClient | None = None,
        linter: StyleLinter | None = None,
        scanner: SecurityScanner | None = None,
        parser: AstParser | None = None,
        use_llm: bool = True,
    ) -> None:
        self.settings = settings
        self.client = client or ClaudeClient(
            settings.anthropic_api_key,
            settings.anthropic_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            max_tokens=settings.llm_max_tokens,
        )
        self.use_llm = use_llm and self.client.enabled
        self.linter = linter or RuffLinter()
        self.scanner = scanner or get_security_scanner(settings.semgrep_bin)
        self.parser = parser or get_parser()
        self.graph = self._build().compile()

    @property
    def mode(self) -> ReviewMode:
        """``"pipeline"`` when Claude participates, ``"linter-only"`` otherwise."""
        return "pipeline" if self.use_llm else "linter-only"

    # ------------------------------------------------------------------ nodes
    def _build(self) -> StateGraph:
        builder: StateGraph = StateGraph(ReviewState)
        builder.add_node("parse_diff", self._parse_diff)
        builder.add_node("build_context", self._build_context)
        builder.add_node("detect_bugs", self._detect_bugs)
        builder.add_node("check_style", self._check_style)
        builder.add_node("scan_security", self._scan_security)
        builder.add_node("review_performance", self._review_performance)
        builder.add_node("analyze_test_gaps", self._analyze_test_gaps)
        builder.add_node("synthesize_comments", self._synthesize)
        builder.add_node("prioritize", self._prioritize)

        builder.set_entry_point("parse_diff")
        builder.add_edge("parse_diff", "build_context")
        for node in ANALYZER_NODES:
            builder.add_edge("build_context", node)
        builder.add_edge(list(ANALYZER_NODES), "synthesize_comments")
        builder.add_edge("synthesize_comments", "prioritize")
        builder.add_edge("prioritize", END)
        return builder

    def _metrics(self) -> RequestMetrics:
        return self._current_metrics

    async def _parse_diff(self, state: ReviewState) -> dict[str, Any]:
        with node_span(self._metrics(), "parse_diff", 0) as span:
            files = parse_unified_diff(state["diff_text"])
            span["comments_out"] = 0
            logger.debug("parsed files={} added={}", len(files), sum(f.n_added for f in files))
            return {"files": files}

    async def _build_context(self, state: ReviewState) -> dict[str, Any]:
        with node_span(self._metrics(), "build_context", 0):
            contexts = build_contexts(state.get("files", []), self.parser)
            return {"contexts": contexts}

    async def _detect_bugs(self, state: ReviewState) -> dict[str, Any]:
        files = state.get("files", [])
        ctx_by_path = {c.path: c for c in state.get("contexts", [])}
        with node_span(self._metrics(), "detect_bugs", 0) as span:
            comments: list[ReviewComment] = []
            errors: list[str] = []
            llm_results = []
            for f in files:
                comments.extend(static_scan(f))
            if self.use_llm:
                detector = ClaudeBugDetector(self.client)
                outcomes = await asyncio.gather(
                    *(detector.review(f, ctx_by_path.get(f.path)) for f in files if f.added),
                    return_exceptions=True,
                )
                for f, outcome in zip([f for f in files if f.added], outcomes, strict=True):
                    if isinstance(outcome, LLMOutputError | anthropic.APIError):
                        msg = f"detect_bugs[{f.path}]: {type(outcome).__name__}: {outcome}"
                        logger.error(msg)
                        errors.append(msg)
                    elif isinstance(outcome, BaseException):
                        raise outcome
                    else:
                        found, usage = outcome
                        comments.extend(found)
                        if usage is not None:
                            llm_results.append(usage)
            span["comments_out"] = len(comments)
            return {"comments": comments, "errors": errors, "llm_results": llm_results}

    async def _check_style(self, state: ReviewState) -> dict[str, Any]:
        with node_span(self._metrics(), "check_style", 0) as span:
            files = state.get("files", [])
            results = await asyncio.gather(
                *(asyncio.to_thread(check_style, f, self.linter) for f in files)
            )
            comments = [c for group in results for c in group]
            span["comments_out"] = len(comments)
            return {"comments": comments}

    async def _scan_security(self, state: ReviewState) -> dict[str, Any]:
        with node_span(self._metrics(), "scan_security", 0) as span:
            files = state.get("files", [])
            results = await asyncio.gather(
                *(asyncio.to_thread(scan_security, f, self.scanner) for f in files)
            )
            comments = [c for group in results for c in group]
            span["comments_out"] = len(comments)
            return {"comments": comments}

    async def _review_performance(self, state: ReviewState) -> dict[str, Any]:
        with node_span(self._metrics(), "review_performance", 0) as span:
            comments = [c for f in state.get("files", []) for c in review_performance(f)]
            span["comments_out"] = len(comments)
            return {"comments": comments}

    async def _analyze_test_gaps(self, state: ReviewState) -> dict[str, Any]:
        with node_span(self._metrics(), "analyze_test_gaps", 0) as span:
            comments = detect_test_gaps(state.get("files", []), state.get("contexts", []))
            span["comments_out"] = len(comments)
            return {"comments": comments}

    async def _synthesize(self, state: ReviewState) -> dict[str, Any]:
        raw = state.get("comments", [])
        with node_span(self._metrics(), "synthesize_comments", len(raw)) as span:
            comments = synthesize_deterministic(raw)
            llm_results = []
            if self.use_llm and comments:
                # Rewrite only what can survive the priority filter to bound cost.
                budget = prioritize(
                    comments,
                    self.settings.max_comments_per_pr * 2,
                    self.settings.max_comments_per_file * 2,
                )
                rewritten, usage = await synthesize_with_claude(
                    budget, state.get("contexts", []), self.client
                )
                by_key = {(c.file, c.line, c.category): c for c in rewritten}
                comments = [by_key.get((c.file, c.line, c.category), c) for c in comments]
                if usage is not None:
                    llm_results.append(usage)
            span["comments_out"] = len(comments)
            return {"synthesized": comments, "llm_results": llm_results}

    async def _prioritize(self, state: ReviewState) -> dict[str, Any]:
        synthesized = state.get("synthesized", [])
        with node_span(self._metrics(), "prioritize", len(synthesized)) as span:
            final = prioritize(
                synthesized, self._current_max_comments, self.settings.max_comments_per_file
            )
            span["comments_out"] = len(final)
            return {"final_comments": final, "summary": summarize(final, state.get("files", []))}

    # ------------------------------------------------------------------ run
    async def run(
        self, diff_text: str, *, max_comments: int | None = None, trace_id: str | None = None
    ) -> ReviewResult:
        """Review ``diff_text`` and return the prioritized comments with metrics.

        The pipeline object is not re-entrant across concurrent calls because it
        keeps the current request's metrics; the API creates one call at a time
        per pipeline instance via ``asyncio.Lock``-free sequential usage or one
        instance per worker.
        """
        metrics = RequestMetrics(trace_id=trace_id) if trace_id else RequestMetrics()
        token = trace_id_var.set(metrics.trace_id)
        self._current_metrics = metrics
        self._current_max_comments = max_comments or self.settings.max_comments_per_pr
        try:
            raw_state = await self.graph.ainvoke(
                {"diff_text": diff_text, "comments": [], "errors": [], "llm_results": []},
                config={
                    "run_name": "code-review",
                    "tags": [self.mode],
                    "metadata": {"trace_id": metrics.trace_id},
                },
            )
            final_state = cast(ReviewState, raw_state)
        finally:
            trace_id_var.reset(token)
        for usage in final_state.get("llm_results", []):
            metrics.add_llm(usage)
        metrics.log_summary()
        return ReviewResult(
            comments=final_state.get("final_comments", []),
            files=final_state.get("files", []),
            contexts=final_state.get("contexts", []),
            metrics=metrics,
            mode=self.mode,
            summary=final_state.get("summary", ""),
            errors=list(final_state.get("errors", [])),
        )
