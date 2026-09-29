"""Baselines for the mandatory comparison table.

* **Linter only** — the graph with every LLM node disabled (static rules + ruff +
  pattern scanner + heuristics). Runs offline.
* **Claude single-pass** — one prompt over the whole diff, no static analysis, no
  synthesizer, no priority filter. Requires ``ANTHROPIC_API_KEY``.
* **Pipeline completo** — the full graph with Claude enabled.
"""

from __future__ import annotations

from typing import Any

from src.analyzers.bug_detector import _coerce_comment
from src.api.schemas import ReviewComment
from src.llm.client import ClaudeClient, LLMOutputError, LLMResult
from src.parser.diff_parser import parse_unified_diff

SINGLE_PASS_SYSTEM = """You are reviewing a pull request. Read the whole unified diff and list the
real problems (bugs, security issues, performance problems, style violations, missing
tests). Return ONLY JSON:
{"comments": [{"file": str, "line": int (new-side line of an ADDED line),
"severity": "error|warning|info", "category": "bug|style|test_gap|security|performance|docs",
"body": str, "suggestion": str|null, "justification": str|null, "confidence": float}]}
At most 10 comments. Do not report anything you are not sure about."""


async def single_pass_review(
    client: ClaudeClient, diff_text: str, *, max_comments: int = 10
) -> tuple[list[ReviewComment], LLMResult | None, list[str]]:
    """One-shot Claude review used as the "single-pass" baseline.

    Returns ``(comments, usage, errors)``; API/parse failures are recorded in
    ``errors`` instead of raised so the eval keeps going.
    """
    files = parse_unified_diff(diff_text)
    by_path = {f.path: f for f in files}
    try:
        payload, usage = await client.complete_json(
            SINGLE_PASS_SYSTEM, f"<diff>\n{diff_text}\n</diff>"
        )
    except LLMOutputError as exc:
        return [], None, [f"single-pass: {exc}"]
    raw: Any = payload.get("comments", [])
    comments: list[ReviewComment] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        file_diff = by_path.get(str(item.get("file", "")))
        if file_diff is None:
            continue
        coerced = _coerce_comment(item, file_diff)
        if coerced is not None:
            comments.append(coerced.model_copy(update={"source": "claude-single-pass"}))
    return comments[:max_comments], usage, []
