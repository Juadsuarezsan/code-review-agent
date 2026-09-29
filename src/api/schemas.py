"""Pydantic models shared by the API, the graph and the evaluation harness."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Severity = Literal["error", "warning", "info"]
Category = Literal["bug", "style", "test_gap", "security", "performance", "docs"]
ReviewMode = Literal["linter-only", "pipeline"]

GITHUB_PR_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>\d+)/?$"
)


class ReviewRequest(BaseModel):
    """Body of ``POST /api/review``.

    Exactly one of ``diff`` or ``pr_url`` must be provided. Size limits are enforced
    here (static) and again in the endpoint against ``MAX_DIFF_BYTES`` (configurable).
    """

    model_config = ConfigDict(extra="forbid")

    diff: str | None = Field(
        default=None,
        description="Unified diff text (``git diff`` / ``gh pr diff`` output).",
        max_length=2_000_000,
    )
    pr_url: str | None = Field(
        default=None,
        description="Public GitHub PR URL, e.g. https://github.com/owner/repo/pull/42",
        max_length=300,
    )
    max_comments: int | None = Field(
        default=None, ge=1, le=100, description="Override MAX_COMMENTS_PER_PR for this request."
    )
    post_to_github: bool = Field(
        default=False, description="Post the review inline on the PR (requires GITHUB_TOKEN)."
    )

    @model_validator(mode="after")
    def _one_input(self) -> ReviewRequest:
        has_diff = bool(self.diff and self.diff.strip())
        has_url = bool(self.pr_url and self.pr_url.strip())
        if has_diff == has_url:
            raise ValueError("Provide exactly one of `diff` or `pr_url`.")
        if has_url and self.pr_url is not None and not GITHUB_PR_URL_RE.match(self.pr_url.strip()):
            raise ValueError("`pr_url` must look like https://github.com/<owner>/<repo>/pull/<n>")
        if self.post_to_github and not has_url:
            raise ValueError("`post_to_github` requires `pr_url`.")
        return self


class ReviewComment(BaseModel):
    """One inline review comment anchored to a line of the new file version."""

    file: str = Field(min_length=1)
    line: int = Field(ge=1)
    severity: Severity
    category: Category
    body: str = Field(min_length=1, max_length=2000)
    suggestion: str | None = Field(default=None, max_length=4000)
    justification: str | None = Field(default=None, max_length=2000)
    rule_id: str | None = None
    source: str = Field(default="static", description="Analyzer that produced the comment.")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class NodeTimingOut(BaseModel):
    """Per-node timing exposed in the API response for observability."""

    node: str
    latency_ms: int
    comments_in: int
    comments_out: int


class ReviewResponse(BaseModel):
    """Result of a review, including cost and latency accounting."""

    trace_id: str
    mode: ReviewMode
    comments: list[ReviewComment] = Field(default_factory=list)
    overall_summary: str = ""
    n_files_changed: int = 0
    n_lines_added: int = 0
    n_lines_removed: int = 0
    latency_ms: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cached: bool = False
    posted_to_github: bool = False
    errors: list[str] = Field(default_factory=list)
    node_timings: list[NodeTimingOut] = Field(default_factory=list)
