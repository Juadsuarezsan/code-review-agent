"""Priority Filter: keep the top-N comments so the developer is not flooded.

Score = severity weight + category weight + confidence. A per-file cap keeps one
noisy file from consuming the whole budget.
"""

from __future__ import annotations

from src.api.schemas import ReviewComment

SEVERITY_WEIGHT: dict[str, float] = {"error": 30.0, "warning": 15.0, "info": 5.0}
CATEGORY_WEIGHT: dict[str, float] = {
    "security": 8.0,
    "bug": 7.0,
    "performance": 4.0,
    "test_gap": 3.0,
    "style": 1.0,
    "docs": 0.5,
}


def score_comment(c: ReviewComment) -> float:
    """Return the priority score of one comment (higher is more important)."""
    return SEVERITY_WEIGHT[c.severity] + CATEGORY_WEIGHT[c.category] + 5.0 * c.confidence


def prioritize(
    comments: list[ReviewComment], max_total: int, max_per_file: int | None = None
) -> list[ReviewComment]:
    """Select the highest-scoring comments under global and per-file caps.

    Args:
        comments: Deduplicated comments.
        max_total: Maximum number of comments returned.
        max_per_file: Optional cap per file path.

    Returns:
        Selected comments ordered by ``(file, line)`` for natural reading.
    """
    ranked = sorted(comments, key=lambda c: (-score_comment(c), c.file, c.line))
    per_file: dict[str, int] = {}
    selected: list[ReviewComment] = []
    for c in ranked:
        if len(selected) >= max_total:
            break
        if max_per_file is not None and per_file.get(c.file, 0) >= max_per_file:
            continue
        per_file[c.file] = per_file.get(c.file, 0) + 1
        selected.append(c)
    return sorted(selected, key=lambda c: (c.file, c.line))
