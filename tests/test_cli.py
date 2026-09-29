"""CLI tests (used by the GitHub Action)."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from src import cli
from src.api.schemas import ReviewComment
from src.config import get_settings

DIFF = "--- a/m.py\n+++ b/m.py\n@@ -1,1 +1,2 @@\n def f(x):\n+    return eval(x)\n"


def test_review_file_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "c.diff"
    path.write_text(DIFF)
    assert cli.main(["review", "--diff-file", str(path), "--no-llm", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "linter-only" and data["comments"]
    assert data["metrics"]["llm_calls"] == 0


def test_review_stdin_markdown(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(DIFF))
    assert cli.main(["review", "--diff-file", "-", "--format", "markdown"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("### Code Review Agent") and "| `m.py` |" in out


def test_malformed_and_missing_inputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.diff"
    bad.write_text("not a diff")
    assert cli.main(["review", "--diff-file", str(bad)]) == 2
    assert cli.main(["review", "--diff-file", str(tmp_path / "missing.diff")]) == 2
    assert "error:" in capsys.readouterr().err


@respx.mock
def test_review_pr_and_post(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "github_token", "tok")
    base = settings.github_api_url
    respx.get(f"{base}/repos/o/r/pulls/9").mock(
        side_effect=[
            httpx.Response(200, text=DIFF),
            httpx.Response(200, json={"head": {"sha": "s"}}),
        ]
    )
    posted = respx.post(f"{base}/repos/o/r/pulls/9/reviews").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    rc = cli.main(
        ["review-pr", "--url", "https://github.com/o/r/pull/9", "--post", "--format", "json"]
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["posted_to_github"] is True and posted.called


@respx.mock
def test_review_pr_github_error(capsys: pytest.CaptureFixture[str]) -> None:
    base = get_settings().github_api_url
    respx.get(f"{base}/repos/o/r/pulls/9").mock(return_value=httpx.Response(404))
    assert cli.main(["review-pr", "--url", "https://github.com/o/r/pull/9"]) == 1
    assert "github error" in capsys.readouterr().err


def test_has_blocking_findings() -> None:
    err = ReviewComment(file="a", line=1, severity="error", category="bug", body="x")
    info = ReviewComment(file="a", line=1, severity="info", category="bug", body="x")
    assert cli.has_blocking_findings([info, err]) and not cli.has_blocking_findings([info])
