"""Shared fixtures: deterministic environment, settings and a fake Claude client.

No test touches the network: Anthropic calls go through :class:`FakeClaudeClient`
(or a mocked SDK in ``test_llm_client``), GitHub calls are intercepted by ``respx``
and subprocesses (ruff/semgrep) are either real local binaries or mocked.
"""

from __future__ import annotations

import json
import os
import random
from typing import Any

import pytest

# Environment must be fixed before ``src.api.main`` is imported (it reads settings at import).
for _var in (
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "DATABASE_URL",
    "SEMGREP_BIN",
    "LANGCHAIN_API_KEY",
):
    os.environ.pop(_var, None)
os.environ["RATE_LIMIT"] = "1000/minute"
os.environ["GITHUB_API_URL"] = "https://api.github.test"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from src.config import PINNED_MODEL, Settings, get_settings  # noqa: E402
from src.llm.client import LLMResult, extract_json_object  # noqa: E402
from src.llm.pricing import estimate_cost_usd  # noqa: E402

SEED = 20260516


@pytest.fixture(autouse=True)
def _seed() -> None:
    random.seed(SEED)


@pytest.fixture(scope="session", autouse=True)
def _settings_cache() -> None:
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """Default settings without reading any ``.env`` file (no API keys)."""
    return Settings(_env_file=None)


class FakeClaudeClient:
    """Drop-in replacement for :class:`src.llm.client.ClaudeClient`.

    ``responses`` is consumed in order; each entry is a dict (serialized to JSON),
    a raw string, or an exception instance to raise.
    """

    def __init__(
        self,
        responses: list[Any] | None = None,
        *,
        enabled: bool = True,
        model: str = PINNED_MODEL,
        default: Any | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.enabled = enabled
        self.model = model
        self.default = default
        self.calls: list[tuple[str, str]] = []

    async def complete(
        self, system: str, user: str, *, max_tokens: int | None = None, temperature: float = 0.0
    ) -> LLMResult:
        self.calls.append((system, user))
        if not self.responses:
            if self.default is None:
                raise AssertionError("FakeClaudeClient has no responses left")
            item = self.default
        else:
            item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        text = item if isinstance(item, str) else json.dumps(item)
        return LLMResult(
            text=text,
            input_tokens=200,
            output_tokens=80,
            cost_usd=estimate_cost_usd(self.model, 200, 80),
            latency_ms=5,
            model=self.model,
            stop_reason="end_turn",
        )

    async def complete_json(
        self, system: str, user: str, *, max_tokens: int | None = None
    ) -> tuple[dict[str, Any], LLMResult]:
        result = await self.complete(system, user, max_tokens=max_tokens)
        return extract_json_object(result.text), result
