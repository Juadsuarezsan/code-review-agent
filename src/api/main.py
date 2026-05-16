"""Code Review Agent API."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

load_dotenv()

from src.analyzers.orchestrator import review_diff
from src.api.schemas import ReviewRequest, ReviewResponse
from src.config import get_settings
from src.parser.diff_parser import parse_unified_diff


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Code Review Agent",
    version="0.5.0",
    description="Multi-aspect static + Claude code review over a unified diff or PR.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health() -> dict[str, str]:
    s = get_settings()
    return {
        "status": "ok",
        "version": "0.5.0",
        "stage": "substantive",
        "github_token": "set" if s.github_token else "not set",
        "llm_enabled": "yes" if s.anthropic_api_key else "no",
    }


@app.post("/api/review", response_model=ReviewResponse)
async def review(req: ReviewRequest) -> ReviewResponse:
    s = get_settings()
    t0 = time.perf_counter()
    diff_text = req.diff or ""
    if req.pr_url and not diff_text:
        if not s.github_token:
            raise HTTPException(status_code=400, detail="pr_url requires GITHUB_TOKEN")
        diff_text = await _fetch_pr_diff(req.pr_url, s.github_token)
    if not diff_text:
        raise HTTPException(status_code=400, detail="Provide `diff` or `pr_url`")

    files = parse_unified_diff(diff_text)
    comments = await review_diff(
        diff_text, claude_model=s.anthropic_model, api_key=s.anthropic_api_key,
        max_comments=s.max_comments_per_pr,
    )
    summary = _summarize(comments, files)
    return ReviewResponse(
        comments=comments,
        overall_summary=summary,
        n_files_changed=len(files),
        n_lines_added=sum(f.n_added for f in files),
        n_lines_removed=sum(f.n_removed for f in files),
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )


@app.get("/api/eval/run")
async def eval_endpoint() -> dict:
    from src.eval.runner import run_eval
    return await run_eval()


async def _fetch_pr_diff(pr_url: str, token: str) -> str:
    import httpx
    parts = pr_url.rstrip("/").split("/")
    owner, repo, pr_n = parts[-4], parts[-3], parts[-1]
    api = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_n}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(api, headers={"Authorization": f"token {token}",
                                              "Accept": "application/vnd.github.v3.diff"})
        r.raise_for_status()
        return r.text


def _summarize(comments, files) -> str:
    if not comments:
        return f"Reviewed {len(files)} file(s); no issues flagged."
    n_err = sum(1 for c in comments if c.severity == "error")
    n_warn = sum(1 for c in comments if c.severity == "warning")
    return f"{len(comments)} comments across {len(files)} file(s) — {n_err} error, {n_warn} warning."
