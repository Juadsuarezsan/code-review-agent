"""HTTP API tests: health, validation (422), review flow, cache, GitHub paths."""

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.api.main import app

VALID_DIFF = """--- a/m.py
+++ b/m.py
@@ -1,2 +1,4 @@
 def f(x):
+    if x == None:
+        return eval(x)
     return x
"""


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        c.app.state.cache.clear()  # type: ignore[attr-defined]
        yield c


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok" and data["mode"] == "linter-only"
    assert data["model"] == "claude-sonnet-4-5-20250929"
    assert data["security_scanner"] == "pattern" and data["ast_parser"] == "tree-sitter"
    assert "X-Trace-Id" in r.headers


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"diff": ""},
        {"diff": "   "},
        {"diff": "hello world, not a diff"},
        {"diff": VALID_DIFF, "pr_url": "https://github.com/o/r/pull/1"},
        {"pr_url": "https://example.com/not-github"},
        {
            "pr_url": "https://github.com/o/r/pull/1",
            "post_to_github": False,
            "diff": None,
            "extra": 1,
        },
        {"diff": VALID_DIFF, "max_comments": 0},
        {"diff": VALID_DIFF, "post_to_github": True},
    ],
)
def test_invalid_inputs_return_422(client: TestClient, body: dict[str, object]) -> None:
    r = client.post("/api/review", json=body)
    assert r.status_code == 422, r.text


def test_oversized_diff_returns_422(client: TestClient) -> None:
    big = VALID_DIFF + "+" + "x" * 500_000 + "\n"
    r = client.post("/api/review", json={"diff": big})
    assert r.status_code == 422 and "limit" in r.json()["detail"]


def test_review_diff_and_cache(client: TestClient) -> None:
    r = client.post("/api/review", json={"diff": VALID_DIFF}, headers={"X-Trace-Id": "abc123"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["trace_id"] == "abc123" and r.headers["X-Trace-Id"] == "abc123"
    assert data["mode"] == "linter-only" and data["cached"] is False
    assert data["n_files_changed"] == 1 and data["n_lines_added"] == 2
    cats = {c["category"] for c in data["comments"]}
    assert {"security", "style"} <= cats
    assert {t["node"] for t in data["node_timings"]} >= {"parse_diff", "prioritize"}
    assert data["cost_usd"] == 0.0 and data["llm_calls"] == 0

    r2 = client.post("/api/review", json={"diff": VALID_DIFF})
    assert r2.json()["cached"] is True and r2.json()["trace_id"] != "abc123"
    assert client.get("/api/reviews/recent").json()["cache"]["hits"] == 1


@respx.mock
def test_review_from_pr_url(client: TestClient) -> None:
    base = client.app.state.settings.github_api_url  # type: ignore[attr-defined]
    respx.get(f"{base}/repos/o/r/pulls/12").mock(return_value=httpx.Response(200, text=VALID_DIFF))
    r = client.post("/api/review", json={"pr_url": "https://github.com/o/r/pull/12"})
    assert r.status_code == 200 and r.json()["n_files_changed"] == 1


@respx.mock
def test_pr_not_found_and_upstream_errors(client: TestClient) -> None:
    base = client.app.state.settings.github_api_url  # type: ignore[attr-defined]
    respx.get(f"{base}/repos/o/r/pulls/1").mock(return_value=httpx.Response(404))
    assert (
        client.post("/api/review", json={"pr_url": "https://github.com/o/r/pull/1"}).status_code
        == 404
    )
    respx.get(f"{base}/repos/o/r/pulls/2").mock(return_value=httpx.Response(403))
    assert (
        client.post("/api/review", json={"pr_url": "https://github.com/o/r/pull/2"}).status_code
        == 502
    )


@respx.mock
def test_post_to_github_requires_token(client: TestClient) -> None:
    base = client.app.state.settings.github_api_url  # type: ignore[attr-defined]
    respx.get(f"{base}/repos/o/r/pulls/3").mock(return_value=httpx.Response(200, text=VALID_DIFF))
    r = client.post(
        "/api/review", json={"pr_url": "https://github.com/o/r/pull/3", "post_to_github": True}
    )
    assert r.status_code == 403


@respx.mock
def test_post_to_github_with_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = client.app.state.settings  # type: ignore[attr-defined]
    monkeypatch.setattr(settings, "github_token", "tok")
    base = settings.github_api_url
    respx.get(f"{base}/repos/o/r/pulls/4").mock(
        side_effect=[
            httpx.Response(200, text=VALID_DIFF),
            httpx.Response(200, json={"head": {"sha": "s"}}),
        ]
    )
    posted = respx.post(f"{base}/repos/o/r/pulls/4/reviews").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    r = client.post(
        "/api/review", json={"pr_url": "https://github.com/o/r/pull/4", "post_to_github": True}
    )
    assert r.status_code == 200 and r.json()["posted_to_github"] is True
    assert posted.called


def test_eval_results_endpoint(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_main, "ROOT", tmp_path)
    assert client.get("/api/eval/results").status_code == 404
    runs = tmp_path / "eval" / "runs"
    runs.mkdir(parents=True)
    (runs / "2026-01-01-linter.json").write_text(json.dumps({"systems": {}}))
    data = client.get("/api/eval/results").json()
    assert data["run_file"] == "2026-01-01-linter.json"
