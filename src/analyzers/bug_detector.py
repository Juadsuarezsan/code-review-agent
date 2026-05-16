"""Bug detector — static rules + Claude reasoning over added lines."""
from __future__ import annotations

import json
import re

from src.api.schemas import ReviewComment
from src.parser.diff_parser import FileDiff


STATIC_RULES: list[tuple[str, str, str, str]] = [
    # (regex, severity, category, message)
    (r"\bexcept\s*:\s*$",        "error", "bug",      "Bare except clause swallows all errors silently"),
    (r"\bexcept\s+Exception\s*:\s*pass", "error", "bug", "Silently catching Exception hides bugs"),
    (r"==\s*None\b",             "warning", "style",  "Use 'is None' instead of '== None'"),
    (r"\beval\(",                "warning", "security", "eval() can execute arbitrary code"),
    (r"\bexec\(",                "warning", "security", "exec() can execute arbitrary code"),
    (r"shell\s*=\s*True",        "warning", "security", "subprocess shell=True is dangerous with untrusted input"),
    (r"\bos\.system\b",          "warning", "security", "os.system invokes a shell; prefer subprocess.run with list args"),
    (r"\bpickle\.loads?\s*\(",   "warning", "security", "pickle.load on untrusted data is RCE"),
    (r"\bprint\s*\(",            "info",  "style",    "Prefer logging over print() in production code"),
    (r"\bTODO\b",                "info",  "docs",     "TODO marker left in code"),
    (r"\.then\([^)]*\)\.then\(", "info",  "performance", "Long promise chains — consider async/await"),
]


def static_scan(file_diff: FileDiff) -> list[ReviewComment]:
    comments: list[ReviewComment] = []
    for line_no, content in file_diff.added:
        for pattern, severity, category, msg in STATIC_RULES:
            if re.search(pattern, content):
                comments.append(ReviewComment(
                    file=file_diff.path, line=line_no, severity=severity,  # type: ignore[arg-type]
                    category=category, body=msg,  # type: ignore[arg-type]
                    rule_id=f"static:{pattern[:30]}",
                ))
    return comments


CLAUDE_SYSTEM = """You review a code diff for real bugs. Output JSON only:

{
  "comments": [
    { "file": "...", "line": <int>, "severity": "error|warning|info",
      "category": "bug|style|test_gap|security|performance|docs",
      "body": "concrete actionable feedback (1-2 sentences)",
      "suggestion": "code snippet or null" }
  ]
}

Rules:
- ONLY flag REAL issues. Do not nitpick on style if no rule is violated.
- Prefer specificity. "potential issue" comments are useless.
- Maximum 5 comments per file. Pick the most actionable.
- For test gaps: only flag if a public function has new logic without test coverage."""


class ClaudeBugDetector:
    def __init__(self, model: str, api_key: str | None) -> None:
        self.model = model
        self.api_key = api_key

    async def review(self, file_diff: FileDiff) -> list[ReviewComment]:
        if not self.api_key:
            return []
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage, SystemMessage
        chat = ChatAnthropic(model=self.model, api_key=self.api_key, temperature=0, max_tokens=900, timeout=20.0)
        added = "\n".join(f"{n}: {c}" for n, c in file_diff.added[:200])
        removed = "\n".join(f"-{n}: {c}" for n, c in file_diff.removed[:80])
        user = f"<file>{file_diff.path}</file>\n<added>\n{added}\n</added>\n<removed>\n{removed}\n</removed>"
        resp = await chat.ainvoke([SystemMessage(content=CLAUDE_SYSTEM), HumanMessage(content=user)])
        body = resp.content if isinstance(resp.content, str) else str(resp.content)
        body = body.strip()
        if body.startswith("```"):
            body = body.strip("`")
            if body.lower().startswith("json"):
                body = body[4:].lstrip()
        try:
            raw = json.loads(body)
            return [ReviewComment(**c) for c in raw.get("comments", [])][:5]
        except Exception:
            return []
