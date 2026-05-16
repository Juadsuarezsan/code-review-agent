from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["error", "warning", "info"]
Category = Literal["bug", "style", "test_gap", "security", "performance", "docs"]


class ReviewRequest(BaseModel):
    diff: str | None = None
    pr_url: str | None = None
    files: dict[str, str] | None = Field(default=None, description="filename -> file content (for non-diff reviews)")
    language: Literal["python", "javascript", "typescript", "auto"] = "auto"


class ReviewComment(BaseModel):
    file: str
    line: int
    severity: Severity
    category: Category
    body: str
    suggestion: str | None = None
    rule_id: str | None = None


class ReviewResponse(BaseModel):
    comments: list[ReviewComment] = Field(default_factory=list)
    overall_summary: str = ""
    n_files_changed: int = 0
    n_lines_added: int = 0
    n_lines_removed: int = 0
    latency_ms: int = 0
