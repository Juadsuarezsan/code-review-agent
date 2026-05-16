"""Code Review Agent — placeholder until v0.1.0 build out."""
from __future__ import annotations

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

from src.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Code Review Agent",
    version="0.1.0",
    description="Code Review Agent — tree-sitter + multi-aspect analyzers + GitHub Action",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "version": "0.1.0",
        "stage": "scaffolding",
        "llm_enabled": "yes" if s.anthropic_api_key else "no",
    }

class ReviewRequest(BaseModel):
    diff: str | None = None
    pr_url: str | None = None


class ReviewComment(BaseModel):
    file: str
    line: int
    severity: str  # error | warning | info
    category: str  # bug | style | test_gap | security | performance
    body: str


class ReviewResponse(BaseModel):
    comments: list[ReviewComment] = []
    overall_summary: str = ""
    n_files_changed: int = 0
    latency_ms: int = 0


@app.post("/api/review", response_model=ReviewResponse)
async def review(req: ReviewRequest) -> ReviewResponse:
    return ReviewResponse(comments=[], overall_summary="not_yet_implemented")
