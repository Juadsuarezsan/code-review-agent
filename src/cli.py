"""Command-line interface used locally and by the GitHub Action.

Examples::

    python -m src.cli review --diff-file changes.diff
    git diff main | python -m src.cli review --diff-file -
    python -m src.cli review-pr --url https://github.com/o/r/pull/1 --post
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from src.api.schemas import ReviewComment
from src.config import get_settings
from src.github.client import GitHubClient, GitHubError, parse_pr_url
from src.graph.pipeline import ReviewPipeline, ReviewResult
from src.observability import configure_logging
from src.parser.diff_parser import DiffParseError, parse_unified_diff_strict


def render_markdown(result: ReviewResult) -> str:
    """Render a review as a Markdown table (used for PR summary comments)."""
    lines = [
        f"### Code Review Agent — {result.summary}",
        "",
        f"_mode: `{result.mode}` · llm calls: {result.metrics.llm_calls} · "
        f"cost: ${result.metrics.cost_usd:.4f} · latency: {result.metrics.latency_ms} ms_",
        "",
    ]
    if result.comments:
        lines += ["| File | Line | Severity | Category | Comment |", "|---|---|---|---|---|"]
        for c in result.comments:
            body = c.body.replace("|", "\\|")
            lines.append(f"| `{c.file}` | {c.line} | {c.severity} | {c.category} | {body} |")
    if result.errors:
        lines += ["", "**Analyzer errors:**"] + [f"- {e}" for e in result.errors]
    return "\n".join(lines)


def result_to_dict(result: ReviewResult) -> dict[str, Any]:
    """JSON-serialisable view of a :class:`ReviewResult`."""
    return {
        "mode": result.mode,
        "summary": result.summary,
        "comments": [c.model_dump() for c in result.comments],
        "metrics": result.metrics.as_dict(),
        "errors": result.errors,
        "n_files_changed": len(result.files),
    }


async def _review_text(diff_text: str, max_comments: int | None, use_llm: bool) -> ReviewResult:
    settings = get_settings()
    pipeline = ReviewPipeline(settings, use_llm=use_llm)
    parse_unified_diff_strict(diff_text)
    return await pipeline.run(diff_text, max_comments=max_comments)


async def _review_pr(
    url: str, max_comments: int | None, use_llm: bool, post: bool
) -> tuple[ReviewResult, bool]:
    settings = get_settings()
    ref = parse_pr_url(url)
    github = GitHubClient(
        settings.github_token,
        base_url=settings.github_api_url,
        timeout_seconds=settings.github_timeout_seconds,
    )
    diff_text = await github.get_pull_diff(ref)
    result = await _review_text(diff_text, max_comments, use_llm)
    posted = False
    if post:
        await github.post_review(ref, result.comments, body=render_markdown(result))
        posted = True
    return result, posted


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse CLI."""
    parser = argparse.ArgumentParser(prog="code-review-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_review = sub.add_parser("review", help="review a unified diff from a file or stdin")
    p_review.add_argument("--diff-file", required=True, help="path to a diff, or '-' for stdin")
    p_review.add_argument("--max-comments", type=int, default=None)
    p_review.add_argument("--no-llm", action="store_true", help="force linter-only mode")
    p_review.add_argument("--format", choices=["json", "markdown"], default="json")

    p_pr = sub.add_parser("review-pr", help="review a GitHub pull request by URL")
    p_pr.add_argument("--url", required=True)
    p_pr.add_argument("--max-comments", type=int, default=None)
    p_pr.add_argument("--no-llm", action="store_true")
    p_pr.add_argument(
        "--post", action="store_true", help="post inline comments (needs GITHUB_TOKEN)"
    )
    p_pr.add_argument("--format", choices=["json", "markdown"], default="markdown")
    return parser


def _emit(result: ReviewResult, fmt: str, posted: bool = False) -> None:
    if fmt == "json":
        data = result_to_dict(result)
        data["posted_to_github"] = posted
        sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
    else:
        sys.stdout.write(render_markdown(result) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code (1 on failure, 2 on bad input)."""
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    try:
        if args.command == "review":
            diff_text = (
                sys.stdin.read()
                if args.diff_file == "-"
                else open(args.diff_file, encoding="utf-8").read()  # noqa: SIM115
            )
            result = asyncio.run(_review_text(diff_text, args.max_comments, not args.no_llm))
            _emit(result, args.format)
        else:
            result, posted = asyncio.run(
                _review_pr(args.url, args.max_comments, not args.no_llm, args.post)
            )
            _emit(result, args.format, posted)
    except (DiffParseError, ValueError, FileNotFoundError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except GitHubError as exc:
        sys.stderr.write(f"github error: {exc}\n")
        return 1
    return 0


def has_blocking_findings(comments: list[ReviewComment]) -> bool:
    """True when any comment is an error (used by the Action's ``fail-on-error`` input)."""
    return any(c.severity == "error" for c in comments)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
