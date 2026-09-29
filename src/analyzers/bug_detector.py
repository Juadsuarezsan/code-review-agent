"""Bug Detector: deterministic static rules plus optional Claude reasoning.

The static rules run on every request and cost nothing. The Claude pass runs only
when a :class:`~src.llm.client.ClaudeClient` is enabled and adds semantic bugs
(logic errors, wrong return values, race conditions) that regexes cannot see.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from loguru import logger

from src.api.schemas import Category, ReviewComment, Severity
from src.context.builder import FileContext
from src.llm.client import ClaudeClient, LLMOutputError, LLMResult
from src.parser.diff_parser import FileDiff


@dataclass(frozen=True)
class StaticRule:
    """One regex rule applied to added lines."""

    rule_id: str
    pattern: re.Pattern[str]
    severity: Severity
    category: Category
    message: str
    suggestion: str | None = None
    justification: str | None = None
    confidence: float = 0.8
    python_only: bool = True
    skip_test_files: bool = False


def _r(
    rule_id: str,
    pattern: str,
    severity: Severity,
    category: Category,
    message: str,
    *,
    suggestion: str | None = None,
    justification: str | None = None,
    confidence: float = 0.8,
    python_only: bool = True,
    skip_test_files: bool = False,
) -> StaticRule:
    return StaticRule(
        rule_id,
        re.compile(pattern),
        severity,
        category,
        message,
        suggestion,
        justification,
        confidence,
        python_only,
        skip_test_files,
    )


STATIC_RULES: list[StaticRule] = [
    _r(
        "bare-except",
        r"^\s*except\s*:\s*(#.*)?$",
        "error",
        "bug",
        "Bare `except:` swallows every error, including KeyboardInterrupt and SystemExit.",
        suggestion="except (ValueError, KeyError) as exc:  # name the exceptions you expect",
        justification="Broad handlers hide real failures and make debugging impossible.",
        confidence=0.9,
    ),
    _r(
        "mutable-default-arg",
        r"^\s*(async\s+)?def\s+\w+\s*\(.*=\s*(\[\]|\{\}|set\(\)|list\(\)|dict\(\))",
        "warning",
        "bug",
        "Mutable default argument is shared across calls.",
        suggestion=(
            "def f(items: list[str] | None = None):\n" "    items = [] if items is None else items"
        ),
        justification="Default values are evaluated once at definition time.",
        confidence=0.9,
    ),
    _r(
        "is-literal",
        r"\bis\s+(not\s+)?(\d+|\"[^\"]*\"|'[^']*'|\[\]|\{\})",
        "error",
        "bug",
        "`is` compares identity, not equality; comparing with a literal is a bug.",
        suggestion="Use `==` / `!=` for value comparison.",
        confidence=0.9,
    ),
    _r(
        "eq-none",
        r"(==|!=)\s*None\b",
        "warning",
        "style",
        "Compare with `None` using `is` / `is not`.",
        suggestion="if value is None:",
        confidence=0.9,
    ),
    _r(
        "return-in-finally",
        r"^\s*return\b.*#\s*finally|^\s{4,}return\b(?=.*)$",
        "info",
        "bug",
        "`return` inside `finally` discards any in-flight exception.",
        confidence=0.3,
    ),
    _r(
        "sort-result-assigned",
        r"=\s*\w+(\.\w+)*\.sort\(\)",
        "error",
        "bug",
        "`list.sort()` returns None; the assignment discards the list.",
        suggestion="items.sort()  # in place\n# or: ordered = sorted(items)",
        confidence=0.95,
    ),
    _r(
        "range-len-plus-one",
        r"range\(\s*len\([^)]*\)\s*\+\s*1\s*\)",
        "warning",
        "bug",
        "`range(len(x) + 1)` iterates one past the last index (IndexError).",
        confidence=0.7,
    ),
    _r(
        "open-without-with",
        r"^\s*\w+\s*=\s*open\(",
        "warning",
        "bug",
        "File handle opened without a context manager may leak.",
        suggestion="with open(path, encoding='utf-8') as fh:",
        confidence=0.6,
    ),
    _r(
        "assert-in-prod",
        r"^\s*assert\s+",
        "warning",
        "bug",
        "`assert` is stripped with `python -O`; do not use it for runtime validation.",
        suggestion="if not condition:\n    raise ValueError('...')",
        confidence=0.6,
        skip_test_files=True,
    ),
    _r(
        "type-equality",
        r"\btype\([^)]*\)\s*(==|!=)\s*",
        "info",
        "style",
        "Use `isinstance()` instead of comparing `type()` results.",
        confidence=0.7,
    ),
    _r(
        "except-base-exception",
        r"^\s*except\s+BaseException\b",
        "warning",
        "bug",
        "Catching BaseException also traps KeyboardInterrupt/SystemExit.",
        confidence=0.7,
    ),
    _r(
        "shadow-builtin",
        r"^\s*(id|list|dict|type|input|str|len|max|min|sum|filter|map)\s*=\s*",
        "info",
        "style",
        "Assignment shadows a Python builtin.",
        confidence=0.6,
    ),
    _r(
        "print-in-prod",
        r"^\s*print\s*\(",
        "info",
        "style",
        "Prefer structured logging over `print()` in production code.",
        suggestion="logger.info('...')",
        confidence=0.6,
        skip_test_files=True,
    ),
    _r(
        "todo-marker",
        r"\b(TODO|FIXME|XXX|HACK)\b",
        "info",
        "docs",
        "TODO/FIXME marker left in code; link it to an issue or resolve it.",
        confidence=0.9,
        python_only=False,
    ),
    _r(
        "division-by-len",
        r"/\s*len\(",
        "info",
        "bug",
        "Division by `len(...)` raises ZeroDivisionError on empty input.",
        suggestion="total / len(items) if items else 0.0",
        confidence=0.5,
    ),
    _r(
        "js-eqeq",
        r"[^=!]==[^=]",
        "info",
        "style",
        "Use strict equality `===` in JavaScript/TypeScript.",
        confidence=0.5,
        python_only=False,
    ),
]

_EXCEPT_RE = re.compile(r"^\s*except\b[^:]*:\s*(#.*)?$")
_PASS_RE = re.compile(r"^\s*pass\s*(#.*)?$")
_FOR_RE = re.compile(r"^\s*for\s+\w+\s+in\s+(\w+)\s*:")
_MUTATE_RE = re.compile(r"^\s*(\w+)\.(remove|pop|append|insert|clear)\(")


def _language_ok(rule: StaticRule, file_diff: FileDiff) -> bool:
    if rule.rule_id == "js-eqeq":
        return file_diff.language in ("javascript", "typescript")
    if rule.python_only:
        return file_diff.language == "python"
    return True


def _multiline_rules(file_diff: FileDiff) -> list[ReviewComment]:
    """Rules that need to look at neighbouring new-side lines."""
    comments: list[ReviewComment] = []
    if file_diff.language != "python":
        return comments
    lines = file_diff.new_lines
    added = file_diff.added_line_numbers
    ordered = sorted(lines)
    for idx, line_no in enumerate(ordered):
        content = lines[line_no]
        if line_no in added and _EXCEPT_RE.match(content):
            nxt = next((lines[n] for n in ordered[idx + 1 :] if lines[n].strip()), None)
            if nxt is not None and _PASS_RE.match(nxt):
                comments.append(
                    ReviewComment(
                        file=file_diff.path,
                        line=line_no,
                        severity="error",
                        category="bug",
                        body="Exception handler silently swallows the error with `pass`.",
                        suggestion=(
                            "except SomeError as exc:\n"
                            "    logger.warning('...: %s', exc)\n    raise"
                        ),
                        justification="Silent failures hide bugs and corrupt state downstream.",
                        rule_id="static:swallowed-exception",
                        source="static",
                        confidence=0.9,
                    )
                )
        m_for = _FOR_RE.match(content)
        if m_for:
            iterable = m_for.group(1)
            indent = len(content) - len(content.lstrip())
            for later in ordered[idx + 1 : idx + 12]:
                body = lines[later]
                if body.strip() and (len(body) - len(body.lstrip())) <= indent:
                    break
                m_mut = _MUTATE_RE.match(body)
                if m_mut and m_mut.group(1) == iterable and later in added:
                    comments.append(
                        ReviewComment(
                            file=file_diff.path,
                            line=later,
                            severity="error",
                            category="bug",
                            body=(
                                f"`{iterable}` is mutated while being iterated; "
                                "elements get skipped."
                            ),
                            suggestion=f"for item in list({iterable}):  # iterate over a copy",
                            justification="Mutating a list during iteration shifts indices.",
                            rule_id="static:mutate-while-iterating",
                            source="static",
                            confidence=0.85,
                        )
                    )
                    break
    return comments


def static_scan(file_diff: FileDiff) -> list[ReviewComment]:
    """Apply every static rule to the added lines of ``file_diff``."""
    comments: list[ReviewComment] = []
    for line_no, content in file_diff.added:
        stripped = content.strip()
        if not stripped:
            continue
        is_comment = stripped.startswith("#")
        for rule in STATIC_RULES:
            if not _language_ok(rule, file_diff):
                continue
            if rule.skip_test_files and file_diff.is_test_file:
                continue
            if rule.rule_id == "return-in-finally":
                continue  # needs block context; handled by Claude pass
            if is_comment and rule.rule_id != "todo-marker":
                continue
            if rule.pattern.search(content):
                comments.append(
                    ReviewComment(
                        file=file_diff.path,
                        line=line_no,
                        severity=rule.severity,
                        category=rule.category,
                        body=rule.message,
                        suggestion=rule.suggestion,
                        justification=rule.justification,
                        rule_id=f"static:{rule.rule_id}",
                        source="static",
                        confidence=rule.confidence,
                    )
                )
    comments.extend(_multiline_rules(file_diff))
    return comments


CLAUDE_SYSTEM = """You are a senior engineer reviewing a pull request diff for REAL defects.
Return ONLY a JSON object with this shape:

{"comments": [
  {"file": "<path>", "line": <new-side line number of an added line>,
   "severity": "error|warning|info",
   "category": "bug|style|test_gap|security|performance|docs",
   "body": "one or two sentences, concrete and actionable",
   "suggestion": "replacement code or null",
   "justification": "why this is a problem, one sentence",
   "confidence": <0.0-1.0>}
]}

Rules:
1. Flag only issues you are confident about; skip speculation and pure style nits.
2. Anchor every comment to an ADDED line number from the <added> block.
3. Prefer logic errors, wrong return values, off-by-one, None handling, concurrency,
   resource leaks and misuse of the imported APIs listed in <context>.
4. At most 5 comments. If the change is fine, return {"comments": []}."""


def build_bug_prompt(file_diff: FileDiff, context: FileContext | None) -> str:
    """Build the user prompt for the Claude bug pass."""
    added = "\n".join(f"{n}: {c}" for n, c in file_diff.added[:250])
    removed = "\n".join(f"-{n}: {c}" for n, c in file_diff.removed[:100])
    ctx = context.summary() if context else "(no structural context)"
    return (
        f"<file>{file_diff.path}</file>\n<context>\n{ctx}\n</context>\n"
        f"<added>\n{added}\n</added>\n<removed>\n{removed}\n</removed>"
    )


def _coerce_comment(raw: dict[str, Any], file_diff: FileDiff) -> ReviewComment | None:
    """Validate one model-produced comment; return None when it cannot be anchored."""
    try:
        line = int(raw.get("line", 0))
    except (TypeError, ValueError):
        return None
    if line not in file_diff.added_line_numbers:
        # Snap to the nearest added line within 3 lines, else drop.
        near = [n for n in file_diff.added_line_numbers if abs(n - line) <= 3]
        if not near:
            return None
        line = min(near, key=lambda n: abs(n - line))
    try:
        return ReviewComment(
            file=file_diff.path,
            line=line,
            severity=raw.get("severity", "warning"),
            category=raw.get("category", "bug"),
            body=str(raw.get("body", "")).strip() or "Potential defect flagged by the reviewer.",
            suggestion=(str(raw["suggestion"]) if raw.get("suggestion") else None),
            justification=(str(raw["justification"]) if raw.get("justification") else None),
            rule_id="claude:bug",
            source="claude",
            confidence=float(raw.get("confidence", 0.7)),
        )
    except ValueError as exc:
        logger.debug("dropping malformed model comment: {}", exc)
        return None


class ClaudeBugDetector:
    """Semantic bug pass over one file using Claude."""

    def __init__(self, client: ClaudeClient) -> None:
        self.client = client

    async def review(
        self, file_diff: FileDiff, context: FileContext | None = None
    ) -> tuple[list[ReviewComment], LLMResult | None]:
        """Return validated comments and the LLM usage for the call.

        Raises:
            LLMOutputError: When the model does not return the expected JSON.
            anthropic.APIError: On API failures after retries.
        """
        if not self.client.enabled or not file_diff.added:
            return [], None
        payload, result = await self.client.complete_json(
            CLAUDE_SYSTEM, build_bug_prompt(file_diff, context)
        )
        raw_comments = payload.get("comments")
        if not isinstance(raw_comments, list):
            raise LLMOutputError("`comments` key missing or not a list in model output")
        comments = [
            c
            for c in (_coerce_comment(r, file_diff) for r in raw_comments if isinstance(r, dict))
            if c is not None
        ][:5]
        return comments, result
