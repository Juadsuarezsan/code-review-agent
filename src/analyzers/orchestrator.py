"""Multi-aspect analyzer orchestration."""
from __future__ import annotations

import asyncio

from src.analyzers.bug_detector import ClaudeBugDetector, static_scan
from src.analyzers.test_gap import detect_test_gaps
from src.api.schemas import ReviewComment
from src.parser.diff_parser import FileDiff, parse_unified_diff


SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


async def review_diff(diff_text: str, *, claude_model: str | None, api_key: str | None, max_comments: int) -> list[ReviewComment]:
    files = parse_unified_diff(diff_text)
    if not files:
        return []
    all_comments: list[ReviewComment] = []

    # Static rules + Claude per file (parallel)
    detector = ClaudeBugDetector(model=claude_model or "claude-sonnet-4-5", api_key=api_key)
    tasks = []
    for f in files:
        all_comments.extend(static_scan(f))
        tasks.append(detector.review(f))
    for results in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(results, list):
            all_comments.extend(results)

    # Test-gap (cross-file)
    all_comments.extend(detect_test_gaps(files))

    # Dedupe (file, line, body)
    seen: set[tuple[str, int, str]] = set()
    deduped: list[ReviewComment] = []
    for c in all_comments:
        key = (c.file, c.line, c.body)
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    # Prioritize by severity, then keep top-N
    deduped.sort(key=lambda c: (SEVERITY_ORDER[c.severity], c.file, c.line))
    return deduped[:max_comments]
