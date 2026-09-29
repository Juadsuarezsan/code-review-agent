"""Comment Synthesizer: merge analyzer output into clean, well-formed review comments.

Deterministic steps (always run): sanitize text, deduplicate by ``(file, line,
category)`` keeping the strongest evidence, and fill in missing justification.
Optional step (when Claude is enabled): rewrite the surviving comments so that
each one states location, severity, suggestion and justification consistently.
"""

from __future__ import annotations

import html
import re
from typing import Any

from loguru import logger

from src.api.schemas import ReviewComment
from src.context.builder import FileContext
from src.llm.client import ClaudeClient, LLMOutputError, LLMResult

SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}
_TAG_RE = re.compile(r"<[^>]{1,80}>")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(text: str | None, limit: int = 2000) -> str | None:
    """Strip HTML tags and control characters from model or tool output."""
    if text is None:
        return None
    cleaned = _CONTROL_RE.sub("", _TAG_RE.sub("", html.unescape(text))).strip()
    return cleaned[:limit] or None


def _key(c: ReviewComment) -> tuple[str, int, str]:
    return (c.file, c.line, c.category)


def _stronger(a: ReviewComment, b: ReviewComment) -> ReviewComment:
    """Pick the comment with higher severity, then higher confidence; merge rule ids."""
    winner, loser = (
        (a, b)
        if (SEVERITY_RANK[a.severity], -a.confidence)
        <= (
            SEVERITY_RANK[b.severity],
            -b.confidence,
        )
        else (b, a)
    )
    rule_ids = [r for r in (winner.rule_id, loser.rule_id) if r]
    merged = winner.model_copy(
        update={
            "rule_id": " + ".join(dict.fromkeys(rule_ids)) or None,
            "suggestion": winner.suggestion or loser.suggestion,
            "justification": winner.justification or loser.justification,
            "confidence": max(winner.confidence, loser.confidence),
        }
    )
    return merged


def dedupe_comments(comments: list[ReviewComment]) -> list[ReviewComment]:
    """Collapse comments that point at the same line and category."""
    merged: dict[tuple[str, int, str], ReviewComment] = {}
    for c in comments:
        k = _key(c)
        merged[k] = _stronger(merged[k], c) if k in merged else c
    return sorted(merged.values(), key=lambda c: (c.file, c.line, SEVERITY_RANK[c.severity]))


def normalize_comment(c: ReviewComment) -> ReviewComment:
    """Sanitize text fields and make sure the body ends with punctuation."""
    body = sanitize_text(c.body) or "Issue flagged by the reviewer."
    if body[-1] not in ".!?)":
        body += "."
    return c.model_copy(
        update={
            "body": body,
            "suggestion": sanitize_text(c.suggestion, 4000),
            "justification": sanitize_text(c.justification)
            or f"Flagged by {c.source} ({c.rule_id or 'no rule id'}).",
        }
    )


def synthesize_deterministic(comments: list[ReviewComment]) -> list[ReviewComment]:
    """Sanitize, normalize and dedupe without any LLM call."""
    return dedupe_comments([normalize_comment(c) for c in comments])


SYNTH_SYSTEM = """You polish code-review comments produced by automated analyzers.
For each input comment return the same file and line, and rewrite the text so it is:
- specific (name the variable/function), - actionable (say what to change),
- justified (one sentence on why). Keep severity unless clearly wrong. Never invent new comments.
Return ONLY JSON: {"comments": [{"file": str, "line": int, "severity": "error|warning|info",
"body": str, "suggestion": str|null, "justification": str}]}"""


def build_synth_prompt(comments: list[ReviewComment], contexts: list[FileContext]) -> str:
    """Serialize comments (and touched symbols) for the rewrite prompt."""
    ctx_lines = [f"{c.path}: {c.summary().replace(chr(10), ' | ')}" for c in contexts]
    items = [
        {
            "file": c.file,
            "line": c.line,
            "severity": c.severity,
            "category": c.category,
            "body": c.body,
            "suggestion": c.suggestion,
            "justification": c.justification,
            "rule_id": c.rule_id,
        }
        for c in comments
    ]
    import json

    return (
        f"<context>\n{chr(10).join(ctx_lines)}\n</context>\n"
        f"<comments>\n{json.dumps(items)}\n</comments>"
    )


def _apply_rewrite(original: ReviewComment, raw: dict[str, Any]) -> ReviewComment:
    update: dict[str, Any] = {}
    body = sanitize_text(str(raw.get("body", "")))
    if body:
        update["body"] = body
    if raw.get("suggestion"):
        update["suggestion"] = sanitize_text(str(raw["suggestion"]), 4000)
    if raw.get("justification"):
        update["justification"] = sanitize_text(str(raw["justification"]))
    if raw.get("severity") in SEVERITY_RANK:
        update["severity"] = raw["severity"]
    return original.model_copy(update=update)


async def synthesize_with_claude(
    comments: list[ReviewComment], contexts: list[FileContext], client: ClaudeClient
) -> tuple[list[ReviewComment], LLMResult | None]:
    """Rewrite comments with Claude; fall back to the input on any model failure.

    Comments are matched back by ``(file, line)``; anything the model drops or
    invents is ignored so the set of findings never changes here.
    """
    if not comments or not client.enabled:
        return comments, None
    try:
        payload, result = await client.complete_json(
            SYNTH_SYSTEM, build_synth_prompt(comments, contexts)
        )
    except LLMOutputError as exc:
        logger.error("synthesizer: model output unusable, keeping deterministic comments: {}", exc)
        return comments, None
    by_key = {(c.file, c.line): c for c in comments}
    rewritten: dict[tuple[str, int], ReviewComment] = {}
    for raw in payload.get("comments", []) if isinstance(payload.get("comments"), list) else []:
        if not isinstance(raw, dict):
            continue
        try:
            key = (str(raw.get("file")), int(raw.get("line", 0)))
        except (TypeError, ValueError):
            continue
        if key in by_key and key not in rewritten:
            rewritten[key] = _apply_rewrite(by_key[key], raw)
    return [rewritten.get((c.file, c.line), c) for c in comments], result


def format_comment_markdown(c: ReviewComment) -> str:
    """Render a comment as GitHub-flavoured Markdown for inline posting."""
    icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}[c.severity]
    parts = [f"{icon} **{c.severity.upper()} · {c.category.replace('_', ' ')}** — {c.body}"]
    if c.suggestion:
        parts.append(f"\n```suggestion-hint\n{c.suggestion}\n```")
    if c.justification:
        parts.append(f"\n_Why:_ {c.justification}")
    parts.append(f"\n<sub>rule: `{c.rule_id or 'n/a'}` · confidence {c.confidence:.2f}</sub>")
    return "\n".join(parts)
