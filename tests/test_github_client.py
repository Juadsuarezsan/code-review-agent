"""GitHub REST client tests with respx (no network)."""

import httpx
import pytest
import respx
from pytest_mock import MockerFixture

from src.api.schemas import ReviewComment
from src.github.client import GitHubClient, GitHubError, PullRequestRef, parse_pr_url

BASE = "https://api.github.test"
REF = PullRequestRef("octo", "repo", 7)


def test_parse_pr_url() -> None:
    assert parse_pr_url("https://github.com/octo/repo/pull/7") == REF
    assert parse_pr_url("https://github.com/octo/repo/pull/7/files").number == 7
    with pytest.raises(ValueError):
        parse_pr_url("https://github.com/octo/repo/issues/7")
    assert REF.path == "/repos/octo/repo/pulls/7" and REF.html_url.endswith("/pull/7")


@respx.mock
async def test_get_pull_diff_and_headers() -> None:
    route = respx.get(f"{BASE}/repos/octo/repo/pulls/7").mock(
        return_value=httpx.Response(200, text="diff --git a/x b/x\n")
    )
    client = GitHubClient("tok", base_url=BASE)
    assert (await client.get_pull_diff(REF)).startswith("diff --git")
    headers = route.calls.last.request.headers
    assert headers["Accept"] == "application/vnd.github.v3.diff"
    assert headers["Authorization"] == "Bearer tok"


@respx.mock
async def test_get_pull_and_files_without_token() -> None:
    respx.get(f"{BASE}/repos/octo/repo/pulls/7").mock(
        return_value=httpx.Response(200, json={"title": "T", "head": {"sha": "abc"}})
    )
    respx.get(f"{BASE}/repos/octo/repo/pulls/7/files", params={"per_page": "100"}).mock(
        return_value=httpx.Response(200, json=[{"filename": "x.py"}])
    )
    client = GitHubClient(None, base_url=BASE)
    assert (await client.get_pull(REF))["head"]["sha"] == "abc"
    assert (await client.get_pull_files(REF))[0]["filename"] == "x.py"


@respx.mock
async def test_post_review_builds_inline_comments() -> None:
    respx.get(f"{BASE}/repos/octo/repo/pulls/7").mock(
        return_value=httpx.Response(200, json={"head": {"sha": "abc"}})
    )
    route = respx.post(f"{BASE}/repos/octo/repo/pulls/7/reviews").mock(
        return_value=httpx.Response(200, json={"id": 99})
    )
    comment = ReviewComment(file="x.py", line=3, severity="error", category="bug", body="Broken")
    data = await GitHubClient("tok", base_url=BASE).post_review(REF, [comment], body="summary")
    assert data["id"] == 99
    sent = route.calls.last.request
    import json

    payload = json.loads(sent.content)
    assert payload["commit_id"] == "abc" and payload["event"] == "COMMENT"
    assert payload["comments"][0]["path"] == "x.py" and payload["comments"][0]["side"] == "RIGHT"
    assert "Broken" in payload["comments"][0]["body"]


async def test_post_requires_token() -> None:
    client = GitHubClient(None, base_url=BASE)
    with pytest.raises(GitHubError) as exc:
        await client.post_review(REF, [], body="x")
    assert exc.value.status_code == 401
    with pytest.raises(GitHubError):
        await client.post_issue_comment(REF, "hi")


@respx.mock
async def test_retries_on_server_error(mocker: MockerFixture) -> None:
    mocker.patch("src.github.client.wait_exponential", return_value=lambda *_: 0)
    route = respx.get(f"{BASE}/repos/octo/repo/pulls/7").mock(
        side_effect=[httpx.Response(502), httpx.Response(200, text="ok")]
    )
    assert await GitHubClient("tok", base_url=BASE, max_retries=3).get_pull_diff(REF) == "ok"
    assert route.call_count == 2


@respx.mock
async def test_client_errors_do_not_retry() -> None:
    route = respx.get(f"{BASE}/repos/octo/repo/pulls/7").mock(
        return_value=httpx.Response(404, text="nope")
    )
    with pytest.raises(GitHubError) as exc:
        await GitHubClient("tok", base_url=BASE, max_retries=3).get_pull_diff(REF)
    assert exc.value.status_code == 404 and route.call_count == 1


@respx.mock
async def test_transport_error_becomes_github_error(mocker: MockerFixture) -> None:
    mocker.patch("src.github.client.wait_exponential", return_value=lambda *_: 0)
    respx.get(f"{BASE}/repos/octo/repo/pulls/7").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(GitHubError) as exc:
        await GitHubClient("tok", base_url=BASE, max_retries=2).get_pull_diff(REF)
    assert "unreachable" in str(exc.value)


@respx.mock
async def test_post_issue_comment() -> None:
    respx.post(f"{BASE}/repos/octo/repo/issues/7/comments").mock(
        return_value=httpx.Response(201, json={"id": 5})
    )
    assert (await GitHubClient("tok", base_url=BASE).post_issue_comment(REF, "hi"))["id"] == 5
