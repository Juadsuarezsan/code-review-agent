"""ClaudeClient tests with the Anthropic SDK mocked (no network)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import httpx
import pytest
from pytest_mock import MockerFixture

from src.llm.client import ClaudeClient, LLMOutputError, extract_json_object
from src.llm.pricing import estimate_cost_usd


def _response(text: str, stop: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        stop_reason=stop,
    )


def _api_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx.Response(status, request=request), body=None)


def _patch_sdk(mocker: MockerFixture, side_effect: list[object]) -> AsyncMock:
    create = AsyncMock(side_effect=side_effect)
    fake = SimpleNamespace(messages=SimpleNamespace(create=create))
    mocker.patch("src.llm.client.anthropic.AsyncAnthropic", return_value=fake)
    mocker.patch("src.llm.client.wait_exponential", return_value=lambda *_: 0)
    return create


def test_extract_json_object_variants() -> None:
    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('Sure! Here it is: {"a": {"b": 2}} thanks') == {"a": {"b": 2}}
    with pytest.raises(LLMOutputError):
        extract_json_object("[1, 2, 3]")
    with pytest.raises(LLMOutputError):
        extract_json_object("no json here")


def test_pricing_known_and_unknown_models() -> None:
    assert estimate_cost_usd("claude-sonnet-4-5-20250929", 1_000_000, 0) == 3.0
    assert estimate_cost_usd("made-up-model", 0, 1_000_000) == 15.0


async def test_disabled_client_raises() -> None:
    client = ClaudeClient(None, "claude-sonnet-4-5-20250929")
    assert not client.enabled
    with pytest.raises(RuntimeError):
        await client.complete("s", "u")


async def test_complete_json_success(mocker: MockerFixture) -> None:
    create = _patch_sdk(mocker, [_response('{"comments": []}')])
    client = ClaudeClient("key", "claude-sonnet-4-5-20250929", max_tokens=321)
    payload, result = await client.complete_json("sys", "user")
    assert payload == {"comments": []}
    assert result.input_tokens == 100 and result.output_tokens == 50
    assert result.cost_usd == estimate_cost_usd("claude-sonnet-4-5-20250929", 100, 50)
    kwargs = create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-5-20250929" and kwargs["max_tokens"] == 321
    assert kwargs["system"] == "sys" and kwargs["messages"] == [{"role": "user", "content": "user"}]


async def test_retries_rate_limit_then_succeeds(mocker: MockerFixture) -> None:
    create = _patch_sdk(
        mocker,
        [_api_error(anthropic.RateLimitError, 429), _response('{"ok": true}', stop="max_tokens")],
    )
    client = ClaudeClient("key", "m", max_retries=3)
    payload, result = await client.complete_json("s", "u")
    assert payload == {"ok": True} and result.stop_reason == "max_tokens"
    assert create.await_count == 2


async def test_non_retryable_error_is_raised_immediately(mocker: MockerFixture) -> None:
    create = _patch_sdk(mocker, [_api_error(anthropic.BadRequestError, 400)])
    client = ClaudeClient("key", "m", max_retries=3)
    with pytest.raises(anthropic.BadRequestError):
        await client.complete("s", "u")
    assert create.await_count == 1


async def test_retries_exhausted_reraises(mocker: MockerFixture) -> None:
    create = _patch_sdk(mocker, [_api_error(anthropic.InternalServerError, 500)] * 2)
    client = ClaudeClient("key", "m", max_retries=2)
    with pytest.raises(anthropic.InternalServerError):
        await client.complete("s", "u")
    assert create.await_count == 2
