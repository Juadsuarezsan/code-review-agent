"""Performance Reviewer: heuristic detection of common inefficiencies in Python.

The reviewer tracks loop nesting and ``async def`` scope by indentation over the
new-side snippet, so it works on partial diffs where ``ast.parse`` would fail.
Every rule is documented with the complexity problem it targets; confidences are
deliberately moderate because intent cannot be inferred from a fragment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.api.schemas import ReviewComment, Severity
from src.parser.diff_parser import FileDiff

_LOOP_RE = re.compile(r"^\s*(async\s+)?(for|while)\b.*:\s*(#.*)?$")
_ASYNC_DEF_RE = re.compile(r"^\s*async\s+def\b")
_DEF_RE = re.compile(r"^\s*(async\s+)?def\b|^\s*class\b")
_LIST_INIT_RE = re.compile(r"^\s*(\w+)\s*(:\s*[\w\[\], ]+)?\s*=\s*(\[\]|list\(\))\s*$")
_MEMBERSHIP_RE = re.compile(r"\bif\b.*\bnot\s+in\s+(\w+)\b|\bif\b.*\bin\s+(\w+)\b")
_STR_CONCAT_RE = re.compile(r"^\s*(\w+)\s*\+=\s*(f?[\"']|str\(|\w+\s*\+\s*[\"'])")
_LIST_CONCAT_RE = re.compile(r"^\s*(\w+)\s*=\s*\1\s*\+\s*\[")
_RANGE_LEN_RE = re.compile(r"\bfor\s+\w+\s+in\s+range\(\s*len\(")
_RECOMPILE_RE = re.compile(r"\bre\.compile\s*\(")
_SLEEP_RE = re.compile(r"^\s*time\.sleep\s*\(")
_IO_CALL_RE = re.compile(
    r"\b(requests|httpx)\.(get|post|put|delete|patch)\s*\(|\.(execute|executemany|fetchone|fetchall|"
    r"find_one|find|objects\.get|objects\.filter|query)\s*\(|\bawait\s+\w+\.(get|post|fetch|execute)\s*\("
)
_DF_APPEND_RE = re.compile(
    r"\b(pd\.concat|\.append)\s*\(.*(df|frame|DataFrame)|\bDataFrame\.append"
)
_KEYS_MEMBERSHIP_RE = re.compile(r"\bin\s+\w+\.keys\(\)")
_SORTED_INDEX_RE = re.compile(r"\bsorted\([^)]*\)\s*\[\s*(0|-1)\s*\]")
_OPEN_RE = re.compile(r"\bopen\s*\(")
_IMPORT_RE = re.compile(r"^\s*(import|from)\s+\w")
_GLOBAL_LOOKUP_RE = re.compile(r"\blen\(\w+\)\s*(==|>)\s*0\b")


@dataclass
class _Scope:
    indent: int
    kind: str  # "loop" | "async" | "def"


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _comment(
    file_diff: FileDiff,
    line: int,
    severity: Severity,
    rule: str,
    body: str,
    suggestion: str | None,
    justification: str,
    confidence: float,
) -> ReviewComment:
    return ReviewComment(
        file=file_diff.path,
        line=line,
        severity=severity,
        category="performance",
        body=body,
        suggestion=suggestion,
        justification=justification,
        rule_id=f"perf:{rule}",
        source="performance",
        confidence=confidence,
    )


def review_performance(file_diff: FileDiff) -> list[ReviewComment]:
    """Return performance comments for the added lines of one Python file."""
    if file_diff.language != "python" or not file_diff.added:
        return []
    lines = file_diff.new_lines
    added = file_diff.added_line_numbers
    ordered = sorted(lines)
    list_names: set[str] = set()
    for n in ordered:
        m = _LIST_INIT_RE.match(lines[n])
        if m:
            list_names.add(m.group(1))

    scopes: list[_Scope] = []
    comments: list[ReviewComment] = []
    for n in ordered:
        content = lines[n]
        if not content.strip():
            continue
        ind = _indent(content)
        while scopes and ind <= scopes[-1].indent:
            scopes.pop()
        loop_depth = sum(1 for s in scopes if s.kind == "loop")
        in_async = any(s.kind == "async" for s in scopes)
        in_loop = loop_depth > 0

        if n in added:
            comments.extend(
                _rules_for_line(file_diff, n, content, loop_depth, in_loop, in_async, list_names)
            )

        if _LOOP_RE.match(content):
            scopes.append(_Scope(ind, "loop"))
        elif _ASYNC_DEF_RE.match(content):
            scopes.append(_Scope(ind, "async"))
        elif _DEF_RE.match(content):
            scopes.append(_Scope(ind, "def"))
    return comments


def _rules_for_line(
    file_diff: FileDiff,
    n: int,
    content: str,
    loop_depth: int,
    in_loop: bool,
    in_async: bool,
    list_names: set[str],
) -> list[ReviewComment]:
    out: list[ReviewComment] = []
    if _RANGE_LEN_RE.search(content):
        out.append(
            _comment(
                file_diff,
                n,
                "info",
                "range-len",
                "`for i in range(len(x))` is slower and less readable than iterating directly.",
                "for i, item in enumerate(items):",
                "Index-based loops add a lookup per iteration and hide intent.",
                0.6,
            )
        )
    if _LOOP_RE.match(content) and loop_depth >= 2:
        out.append(
            _comment(
                file_diff,
                n,
                "warning",
                "deep-nesting",
                f"Loop nested {loop_depth + 1} levels deep; likely O(n^{loop_depth + 1}).",
                None,
                "Consider a dictionary index, a set, or vectorised operations.",
                0.5,
            )
        )
    if in_loop:
        m_str = _STR_CONCAT_RE.match(content)
        if m_str:
            out.append(
                _comment(
                    file_diff,
                    n,
                    "warning",
                    "quadratic-str-concat",
                    f"String `{m_str.group(1)}` grows by `+=` inside a loop (quadratic copying).",
                    "parts.append(piece)  # then ''.join(parts) after the loop",
                    "Each `+=` copies the whole string; use a list and join once.",
                    0.7,
                )
            )
        m_list = _LIST_CONCAT_RE.match(content)
        if m_list:
            out.append(
                _comment(
                    file_diff,
                    n,
                    "warning",
                    "quadratic-list-concat",
                    f"`{m_list.group(1)} = {m_list.group(1)} + [...]` copies the list "
                    "every iteration.",
                    f"{m_list.group(1)}.append(item)",
                    "List concatenation allocates a new list; append is amortised O(1).",
                    0.8,
                )
            )
        m_mem = _MEMBERSHIP_RE.search(content)
        if m_mem and not content.lstrip().startswith("for "):
            name = m_mem.group(1) or m_mem.group(2)
            if name in list_names:
                out.append(
                    _comment(
                        file_diff,
                        n,
                        "warning",
                        "list-membership-in-loop",
                        f"Membership test `in {name}` on a list inside a loop is O(n) per check.",
                        f"{name}_set = set({name})  # then `in {name}_set`",
                        "Lists scan linearly; sets and dicts test membership in O(1).",
                        0.7,
                    )
                )
        if _RECOMPILE_RE.search(content):
            out.append(
                _comment(
                    file_diff,
                    n,
                    "warning",
                    "recompile-in-loop",
                    "`re.compile` inside a loop recompiles the same pattern on every iteration.",
                    "PATTERN = re.compile(...)  # at module level",
                    "Compiling is expensive relative to matching; hoist it out of the loop.",
                    0.8,
                )
            )
        if _IO_CALL_RE.search(content):
            out.append(
                _comment(
                    file_diff,
                    n,
                    "warning",
                    "n-plus-one",
                    "I/O call (HTTP or database) inside a loop: one round-trip per element (N+1).",
                    "Batch the requests / use `WHERE id IN (...)` / `asyncio.gather`.",
                    "Per-item round-trips dominate latency; batching removes the multiplier.",
                    0.65,
                )
            )
        if _DF_APPEND_RE.search(content):
            out.append(
                _comment(
                    file_diff,
                    n,
                    "warning",
                    "dataframe-append-in-loop",
                    "Growing a DataFrame inside a loop copies all rows each time.",
                    "rows.append(record)  # then pd.DataFrame(rows) once",
                    "pandas concatenation is O(n) per call; collect rows first.",
                    0.75,
                )
            )
        if _OPEN_RE.search(content) and "with" not in content:
            out.append(
                _comment(
                    file_diff,
                    n,
                    "info",
                    "open-in-loop",
                    "File opened inside a loop; open it once outside if the path is constant.",
                    None,
                    "Repeated open/close syscalls add fixed overhead per iteration.",
                    0.4,
                )
            )
        if _IMPORT_RE.match(content):
            out.append(
                _comment(
                    file_diff,
                    n,
                    "info",
                    "import-in-loop",
                    "Import statement inside a loop; move it to module scope.",
                    None,
                    "Import machinery runs a dict lookup every iteration.",
                    0.6,
                )
            )
    if _KEYS_MEMBERSHIP_RE.search(content):
        out.append(
            _comment(
                file_diff,
                n,
                "info",
                "keys-membership",
                "`x in d.keys()` is redundant; test membership on the dict directly.",
                "if key in d:",
                "Same complexity but creates an extra view object.",
                0.7,
            )
        )
    if _SORTED_INDEX_RE.search(content):
        out.append(
            _comment(
                file_diff,
                n,
                "info",
                "sorted-for-extreme",
                "Sorting to take the first/last element is O(n log n); use `min()`/`max()`.",
                "min(items)  # or max(items)",
                "min/max are single-pass O(n).",
                0.75,
            )
        )
    if in_async and _SLEEP_RE.match(content):
        out.append(
            _comment(
                file_diff,
                n,
                "error",
                "blocking-sleep-in-async",
                "`time.sleep` inside an `async def` blocks the whole event loop.",
                "await asyncio.sleep(seconds)",
                "Blocking calls stall every other coroutine.",
                0.9,
            )
        )
    return out
