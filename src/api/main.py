"""Code Review Agent HTTP API.

Endpoints:
    GET  /health              liveness + configuration summary
    POST /api/review          review a unified diff or a GitHub PR URL
    GET  /api/reviews/recent  last reviews (PostgreSQL) or cache statistics
    GET  /api/eval/results    latest committed evaluation run
"""

# NOTE: no ``from __future__ import annotations`` here on purpose: slowapi wraps the
# endpoint and FastAPI must resolve the real ``ReviewRequest`` type, not a string.

import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.api.schemas import NodeTimingOut, ReviewRequest, ReviewResponse
from src.config import Settings, get_settings
from src.github.client import GitHubClient, GitHubError, parse_pr_url
from src.graph.pipeline import ReviewPipeline
from src.observability import configure_logging, new_trace_id, trace_id_var
from src.parser.diff_parser import DiffParseError, parse_unified_diff_strict
from src.storage.cache import ReviewCache, cache_key
from src.storage.postgres import ReviewRepository

load_dotenv()
VERSION = "0.6.0"
ROOT = Path(__file__).resolve().parents[2]
limiter = Limiter(key_func=get_remote_address)


def build_pipeline(settings: Settings) -> ReviewPipeline:
    """Factory used at startup (patched in tests)."""
    return ReviewPipeline(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the pipeline, cache and (optionally) the PostgreSQL repository."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    app.state.settings = settings
    app.state.pipeline = build_pipeline(settings)
    app.state.cache = ReviewCache(settings.review_cache_size)
    app.state.repository = None
    if settings.database_url:
        repo = ReviewRepository.from_url(settings.database_url)
        try:
            await repo.open()
            app.state.repository = repo
        except OSError as exc:
            logger.error("postgres unavailable, continuing without history: {}", exc)
    logger.info("api ready version={} mode={}", VERSION, app.state.pipeline.mode)
    try:
        yield
    finally:
        if app.state.repository is not None:
            await app.state.repository.close()


app = FastAPI(
    title="Code Review Agent",
    version=VERSION,
    description="Multi-aspect (static + Claude) code review over a unified diff or PR URL.",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Trace-Id"],
)


@app.middleware("http")
async def trace_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Attach a ``trace_id`` to every request and echo it in ``X-Trace-Id``."""
    trace_id = request.headers.get("X-Trace-Id") or new_trace_id()
    token = trace_id_var.set(trace_id)
    try:
        response = await call_next(request)
    finally:
        trace_id_var.reset(token)
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Liveness probe with a non-secret configuration summary."""
    settings: Settings = request.app.state.settings
    pipeline: ReviewPipeline = request.app.state.pipeline
    return {
        "status": "ok",
        "version": VERSION,
        "mode": pipeline.mode,
        "model": settings.anthropic_model,
        "llm_enabled": settings.llm_enabled,
        "github_token": "set" if settings.github_token else "not set",
        "security_scanner": pipeline.scanner.name,
        "style_linter": pipeline.linter.name,
        "ast_parser": pipeline.parser.name,
        "langsmith": settings.langchain_tracing_v2 and bool(settings.langchain_api_key),
        "postgres": request.app.state.repository is not None,
        "cache_entries": len(request.app.state.cache),
    }


@app.post("/api/review", response_model=ReviewResponse)
@limiter.limit(get_settings().rate_limit)
async def review(request: Request, payload: ReviewRequest) -> ReviewResponse:
    """Review a diff (or a PR URL) and return prioritized inline comments.

    Returns 422 for empty/oversized/malformed input, 502 when GitHub cannot be
    reached and 403 when posting is requested without a token.
    """
    settings: Settings = request.app.state.settings
    pipeline: ReviewPipeline = request.app.state.pipeline
    cache: ReviewCache = request.app.state.cache
    trace_id = trace_id_var.get()
    github = GitHubClient(
        settings.github_token,
        base_url=settings.github_api_url,
        timeout_seconds=settings.github_timeout_seconds,
    )

    diff_text = payload.diff or ""
    pr_ref = None
    if payload.pr_url:
        pr_ref = parse_pr_url(payload.pr_url)
        try:
            diff_text = await github.get_pull_diff(pr_ref)
        except GitHubError as exc:
            status = 404 if exc.status_code == 404 else 502
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    size = len(diff_text.encode("utf-8"))
    if size > settings.max_diff_bytes:
        raise HTTPException(
            status_code=422,
            detail=f"Diff is {size} bytes; the limit is {settings.max_diff_bytes} bytes.",
        )
    try:
        parse_unified_diff_strict(diff_text)
    except DiffParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    max_comments = payload.max_comments or settings.max_comments_per_pr
    key = cache_key(diff_text, pipeline.mode, max_comments)
    cached = cache.get(key)
    if cached is not None:
        logger.info("cache hit trace_id={}", trace_id)
        return cached.model_copy(update={"trace_id": trace_id, "cached": True})

    result = await pipeline.run(diff_text, max_comments=max_comments, trace_id=trace_id)
    response = ReviewResponse(
        trace_id=trace_id,
        mode=result.mode,
        comments=result.comments,
        overall_summary=result.summary,
        n_files_changed=len(result.files),
        n_lines_added=result.n_lines_added,
        n_lines_removed=result.n_lines_removed,
        latency_ms=result.metrics.latency_ms,
        llm_calls=result.metrics.llm_calls,
        input_tokens=result.metrics.input_tokens,
        output_tokens=result.metrics.output_tokens,
        cost_usd=result.metrics.cost_usd,
        errors=result.errors,
        node_timings=[NodeTimingOut(**vars(t)) for t in result.metrics.node_timings],
    )

    if payload.post_to_github and pr_ref is not None:
        if not settings.github_token:
            raise HTTPException(status_code=403, detail="post_to_github requires GITHUB_TOKEN")
        try:
            await github.post_review(pr_ref, response.comments, body=response.overall_summary)
        except GitHubError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        response.posted_to_github = True

    cache.put(key, response)
    repository: ReviewRepository | None = request.app.state.repository
    if repository is not None:
        try:
            await repository.save(response, hashlib.sha256(diff_text.encode()).hexdigest())
        except OSError as exc:
            logger.error("could not persist review: {}", exc)
    return response


@app.get("/api/reviews/recent")
async def recent_reviews(request: Request, limit: int = 100) -> dict[str, Any]:
    """Last ``limit`` reviews from PostgreSQL, or cache statistics when no DB is configured."""
    cache: ReviewCache = request.app.state.cache
    repository: ReviewRepository | None = request.app.state.repository
    rows: list[dict[str, Any]] = []
    if repository is not None:
        rows = await repository.recent(min(max(limit, 1), 500))
    return {
        "source": "postgres" if repository is not None else "cache",
        "reviews": rows,
        "cache": {"entries": len(cache), "hits": cache.hits, "misses": cache.misses},
    }


@app.get("/api/eval/results")
async def eval_results() -> dict[str, Any]:
    """Return the most recent committed run from ``eval/runs``."""
    runs = sorted((ROOT / "eval" / "runs").glob("*.json"))
    if not runs:
        raise HTTPException(status_code=404, detail="No evaluation runs committed yet.")
    data: dict[str, Any] = json.loads(runs[-1].read_text(encoding="utf-8"))
    data["run_file"] = runs[-1].name
    return data
