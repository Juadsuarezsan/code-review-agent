"""Minimal async GitHub REST client built on ``httpx``.

Only the endpoints the agent needs are implemented: fetch a pull request, its
unified diff and its file list, and post a review with inline comments. Every
call has an explicit timeout and retries transient failures (connection errors,
429, 5xx) with exponential backoff via ``tenacity``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.analyzers.synthesizer import format_comment_markdown
from src.api.schemas import ReviewComment

API_VERSION = "2022-11-28"
_PR_URL_RE = re.compile(r"github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>\d+)")


class GitHubError(RuntimeError):
    """Raised for non-retryable GitHub API failures (4xx) or after retries are exhausted."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class PullRequestRef:
    """Identifies one pull request."""

    owner: str
    repo: str
    number: int

    @property
    def path(self) -> str:
        """``/repos/{owner}/{repo}/pulls/{number}``."""
        return f"/repos/{self.owner}/{self.repo}/pulls/{self.number}"

    @property
    def html_url(self) -> str:
        """Browser URL of the pull request."""
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"


def parse_pr_url(url: str) -> PullRequestRef:
    """Extract ``owner/repo/number`` from a GitHub PR URL.

    Raises:
        ValueError: When the URL does not point at a pull request.
    """
    m = _PR_URL_RE.search(url.strip())
    if not m:
        raise ValueError(f"Not a GitHub pull request URL: {url!r}")
    return PullRequestRef(m.group("owner"), m.group("repo"), int(m.group("number")))


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


class GitHubClient:
    """Async GitHub client scoped to pull-request operations.

    Args:
        token: Personal access token or ``GITHUB_TOKEN``; optional for public reads.
        base_url: API root (override for GitHub Enterprise or tests).
        timeout_seconds: Per-request timeout.
        max_retries: Attempts for retryable errors.
    """

    def __init__(
        self,
        token: str | None,
        *,
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 20.0,
        max_retries: int = 3,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {"Accept": accept, "X-GitHub-Api-Version": API_VERSION}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _request(
        self, method: str, path: str, *, accept: str | None = None, json: Any | None = None
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = self._headers(accept) if accept else self._headers()
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception(_is_retryable),
                stop=stop_after_attempt(self.max_retries),
                wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
                reraise=True,
            ):
                with attempt:
                    if attempt.retry_state.attempt_number > 1:
                        logger.warning(
                            "github retry attempt={} {} {}",
                            attempt.retry_state.attempt_number,
                            method,
                            path,
                        )
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        response = await client.request(method, url, headers=headers, json=json)
                    response.raise_for_status()
                    return response
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300]
            raise GitHubError(
                f"GitHub {method} {path} -> {exc.response.status_code}: {detail}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.TransportError as exc:
            raise GitHubError(f"GitHub {method} {path} unreachable: {exc}") from exc
        raise GitHubError(f"GitHub {method} {path}: retries exhausted")  # pragma: no cover

    async def get_pull(self, ref: PullRequestRef) -> dict[str, Any]:
        """Return the pull request object (title, body, head sha, ...)."""
        response = await self._request("GET", ref.path)
        data: dict[str, Any] = response.json()
        return data

    async def get_pull_diff(self, ref: PullRequestRef) -> str:
        """Return the unified diff of the pull request."""
        response = await self._request("GET", ref.path, accept="application/vnd.github.v3.diff")
        return response.text

    async def get_pull_files(self, ref: PullRequestRef) -> list[dict[str, Any]]:
        """Return the changed files (first page, up to 100 entries)."""
        response = await self._request("GET", f"{ref.path}/files?per_page=100")
        data: list[dict[str, Any]] = response.json()
        return data

    async def post_review(
        self,
        ref: PullRequestRef,
        comments: list[ReviewComment],
        *,
        body: str,
        commit_id: str | None = None,
        event: str = "COMMENT",
    ) -> dict[str, Any]:
        """Create a review with inline comments anchored to new-side lines.

        Raises:
            GitHubError: When no token is configured or GitHub rejects the request.
        """
        if not self.token:
            raise GitHubError("posting a review requires GITHUB_TOKEN", status_code=401)
        if commit_id is None:
            commit_id = str((await self.get_pull(ref))["head"]["sha"])
        payload = {
            "commit_id": commit_id,
            "body": body,
            "event": event,
            "comments": [
                {
                    "path": c.file,
                    "line": c.line,
                    "side": "RIGHT",
                    "body": format_comment_markdown(c),
                }
                for c in comments
            ],
        }
        response = await self._request("POST", f"{ref.path}/reviews", json=payload)
        data: dict[str, Any] = response.json()
        logger.info(
            "posted review id={} comments={} pr={}", data.get("id"), len(comments), ref.html_url
        )
        return data

    async def post_issue_comment(self, ref: PullRequestRef, body: str) -> dict[str, Any]:
        """Post a plain (non-inline) comment on the pull request conversation."""
        if not self.token:
            raise GitHubError("posting a comment requires GITHUB_TOKEN", status_code=401)
        response = await self._request(
            "POST",
            f"/repos/{ref.owner}/{ref.repo}/issues/{ref.number}/comments",
            json={"body": body},
        )
        data: dict[str, Any] = response.json()
        return data
