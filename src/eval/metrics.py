"""Matching and aggregation of review comments against ground truth.

Matching criterion (documented in ``docs/data_schema.md``):

* A comment **matches** a ground-truth item when both point at the same file,
  the comment's category equals the item's category, and the line distance is
  at most ``LINE_TOLERANCE`` (2 lines, to absorb anchoring on the ``def`` line
  versus the offending statement).
* **Recall** = ground-truth items matched by at least one comment / all items.
* **Precision** = comments matching some item / all comments.
* **FP rate** = 1 - precision (share of comments that are noise), also reported
  per clean PR as the mean number of comments on defect-free changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from src.api.schemas import ReviewComment
from src.eval.cases import EvalCase, GroundTruth

LINE_TOLERANCE = 2


@dataclass
class CaseResult:
    """Per-case matching outcome."""

    case_id: str
    title: str
    n_ground_truth: int
    n_comments: int
    matched_truth: list[GroundTruth]
    missed_truth: list[GroundTruth]
    true_positive_comments: list[ReviewComment]
    false_positive_comments: list[ReviewComment]
    latency_ms: int
    cost_usd: float
    clean: bool
    errors: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float | None:
        """Recall for this case, or None when there is no ground truth."""
        if not self.n_ground_truth:
            return None
        return len(self.matched_truth) / self.n_ground_truth

    @property
    def precision(self) -> float | None:
        """Precision for this case, or None when no comments were produced."""
        if not self.n_comments:
            return None
        return len(self.true_positive_comments) / self.n_comments

    def severity_score(self) -> float:
        """How badly the system did (used to rank the worst cases; higher is worse)."""
        missed = len(self.missed_truth)
        noise = len(self.false_positive_comments)
        return missed * 2.0 + noise * 1.0


def _matches(comment: ReviewComment, truth: GroundTruth, tolerance: int) -> bool:
    return (
        comment.file == truth.file
        and comment.category == truth.category
        and abs(comment.line - truth.line) <= tolerance
    )


def match_case(
    case: EvalCase,
    comments: list[ReviewComment],
    *,
    latency_ms: int,
    cost_usd: float,
    errors: list[str] | None = None,
    tolerance: int = LINE_TOLERANCE,
) -> CaseResult:
    """Match ``comments`` against the case's ground truth."""
    matched: list[GroundTruth] = []
    missed: list[GroundTruth] = []
    for truth in case.ground_truth:
        if any(_matches(c, truth, tolerance) for c in comments):
            matched.append(truth)
        else:
            missed.append(truth)
    tps = [c for c in comments if any(_matches(c, t, tolerance) for t in case.ground_truth)]
    fps = [c for c in comments if c not in tps]
    return CaseResult(
        case_id=case.id,
        title=case.title,
        n_ground_truth=len(case.ground_truth),
        n_comments=len(comments),
        matched_truth=matched,
        missed_truth=missed,
        true_positive_comments=tps,
        false_positive_comments=fps,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        clean=case.clean,
        errors=list(errors or []),
    )


@dataclass
class Aggregate:
    """Corpus-level metrics for one system."""

    n_cases: int
    n_ground_truth: int
    n_comments: int
    true_positives: int
    false_positives: int
    matched_truth: int
    recall: float
    precision: float
    f1: float
    fp_rate: float
    comments_per_clean_pr: float
    mean_latency_ms: float
    p95_latency_ms: float
    mean_cost_usd: float
    per_category: dict[str, dict[str, float]]


def _p95(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return float(ordered[idx])


def aggregate(results: list[CaseResult]) -> Aggregate:
    """Aggregate per-case results into corpus metrics."""
    n_gt = sum(r.n_ground_truth for r in results)
    matched = sum(len(r.matched_truth) for r in results)
    n_comments = sum(r.n_comments for r in results)
    tp = sum(len(r.true_positive_comments) for r in results)
    fp = sum(len(r.false_positive_comments) for r in results)
    recall = matched / n_gt if n_gt else 0.0
    precision = tp / n_comments if n_comments else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    clean = [r for r in results if r.clean]

    per_category: dict[str, dict[str, float]] = {}
    categories = sorted({t.category for r in results for t in r.matched_truth + r.missed_truth})
    for cat in categories:
        cat_gt = sum(
            1 for r in results for t in r.matched_truth + r.missed_truth if t.category == cat
        )
        cat_hit = sum(1 for r in results for t in r.matched_truth if t.category == cat)
        cat_comments = sum(
            1
            for r in results
            for c in r.true_positive_comments + r.false_positive_comments
            if c.category == cat
        )
        cat_tp = sum(1 for r in results for c in r.true_positive_comments if c.category == cat)
        per_category[cat] = {
            "ground_truth": float(cat_gt),
            "recall": cat_hit / cat_gt if cat_gt else 0.0,
            "precision": cat_tp / cat_comments if cat_comments else 0.0,
            "comments": float(cat_comments),
        }
    return Aggregate(
        n_cases=len(results),
        n_ground_truth=n_gt,
        n_comments=n_comments,
        true_positives=tp,
        false_positives=fp,
        matched_truth=matched,
        recall=recall,
        precision=precision,
        f1=f1,
        fp_rate=(fp / n_comments) if n_comments else 0.0,
        comments_per_clean_pr=mean([r.n_comments for r in clean]) if clean else 0.0,
        mean_latency_ms=mean([r.latency_ms for r in results]) if results else 0.0,
        p95_latency_ms=_p95([r.latency_ms for r in results]),
        mean_cost_usd=mean([r.cost_usd for r in results]) if results else 0.0,
        per_category=per_category,
    )


def worst_cases(results: list[CaseResult], n: int = 10) -> list[CaseResult]:
    """Return the ``n`` cases with the most misses and noise."""
    return sorted(results, key=lambda r: (-r.severity_score(), r.case_id))[:n]
