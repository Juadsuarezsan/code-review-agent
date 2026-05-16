"""Test gap analyzer — flags public-function changes that aren't covered by test edits."""
from __future__ import annotations

import re

from src.api.schemas import ReviewComment
from src.parser.diff_parser import FileDiff


PUBLIC_FN_RE = re.compile(r"^(async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")


def detect_test_gaps(files: list[FileDiff]) -> list[ReviewComment]:
    public_changes: list[tuple[str, int, str]] = []
    test_changes = False
    for f in files:
        is_test_file = "tests/" in f.path or f.path.startswith("test_") or "/test_" in f.path
        for line_no, content in f.added:
            m = PUBLIC_FN_RE.match(content.lstrip())
            if m and not m.group(2).startswith("_"):
                public_changes.append((f.path, line_no, m.group(2)))
        if is_test_file and f.added:
            test_changes = True

    comments: list[ReviewComment] = []
    if public_changes and not test_changes:
        for path, line, name in public_changes[:3]:
            comments.append(ReviewComment(
                file=path, line=line, severity="warning", category="test_gap",
                body=f"New public function `{name}` has no accompanying test changes.",
                suggestion=f"Add a test in tests/test_{name}.py",
                rule_id="test_gap:public_fn_without_tests",
            ))
    return comments
