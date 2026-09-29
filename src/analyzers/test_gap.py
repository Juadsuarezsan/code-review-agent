"""Test Gap Analyzer: public functions touched by the diff without matching test changes.

When a :class:`~src.context.builder.FileContext` is available, the analyzer uses
the AST symbols (so modified bodies count, not only new ``def`` lines). Otherwise it
falls back to a regex over added ``def`` lines.
"""

from __future__ import annotations

import re

from src.api.schemas import ReviewComment
from src.context.builder import FileContext, Symbol
from src.parser.diff_parser import FileDiff

PUBLIC_FN_RE = re.compile(r"^(async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
MAX_GAPS_PER_FILE = 3


def _public_symbols_from_regex(file_diff: FileDiff) -> list[Symbol]:
    symbols: list[Symbol] = []
    for line_no, content in file_diff.added:
        m = PUBLIC_FN_RE.match(content.lstrip())
        if m and not m.group(2).startswith("_"):
            symbols.append(Symbol(m.group(2), "function", line_no, line_no))
    return symbols


def _touched_public_symbols(file_diff: FileDiff, context: FileContext | None) -> list[Symbol]:
    if context is None or not context.symbols:
        return _public_symbols_from_regex(file_diff)
    seen: set[str] = set()
    out: list[Symbol] = []
    for sym in context.touched_symbols:
        if sym.kind == "class" or sym.name.startswith("_") or sym.qualified_name in seen:
            continue
        seen.add(sym.qualified_name)
        out.append(sym)
    return out


def detect_test_gaps(
    files: list[FileDiff], contexts: list[FileContext] | None = None
) -> list[ReviewComment]:
    """Flag touched public functions that no test change references.

    Args:
        files: All file diffs of the PR.
        contexts: Optional structural contexts aligned with ``files``.

    Returns:
        At most :data:`MAX_GAPS_PER_FILE` comments per source file.
    """
    ctx_by_path = {c.path: c for c in contexts or []}
    test_text = "\n".join(c for f in files if f.is_test_file for _, c in f.added)
    any_test_changes = bool(test_text.strip())
    comments: list[ReviewComment] = []
    for f in files:
        if f.is_test_file or f.language != "python" or f.is_deleted:
            continue
        symbols = _touched_public_symbols(f, ctx_by_path.get(f.path))
        flagged = 0
        for sym in symbols:
            if flagged >= MAX_GAPS_PER_FILE:
                break
            if any_test_changes and re.search(rf"\b{re.escape(sym.name)}\b", test_text):
                continue
            anchor = (
                sym.start_line
                if sym.start_line in f.added_line_numbers
                else next(
                    (n for n in sorted(f.added_line_numbers) if sym.contains(n)), sym.start_line
                )
            )
            if any_test_changes:
                body = (
                    f"`{sym.qualified_name}` changed but the test changes in this PR never "
                    f"reference it."
                )
                confidence = 0.55
            else:
                body = f"`{sym.qualified_name}` changed and the PR adds no tests."
                confidence = 0.7
            module = f.path.rsplit("/", 1)[-1].removesuffix(".py")
            comments.append(
                ReviewComment(
                    file=f.path,
                    line=anchor,
                    severity="warning",
                    category="test_gap",
                    body=body,
                    suggestion=(
                        f"Add `test_{sym.name}` in tests/test_{module}.py "
                        "covering the new branch."
                    ),
                    justification="Untested public behaviour regresses silently.",
                    rule_id="test_gap:public-fn-without-tests",
                    source="test_gap",
                    confidence=confidence,
                )
            )
            flagged += 1
    return comments
