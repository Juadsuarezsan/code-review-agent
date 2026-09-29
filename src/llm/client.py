"""Async Claude client with explicit timeout, exponential-backoff retries and cost accounting.

All LLM calls in the project go through :class:`ClaudeClient`. Retries are handled
here with ``tenacity`` (the SDK's own retries are disabled) so that the retry policy
is visible and testable. Nothing in this module reads the environment: the API key
and model are injected by the caller.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import anthropic
from loguru import logger
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.llm.pricing import estimate_cost_usd

RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


class LLMOutputError(ValueError):
    """Raised when the model's answer cannot be parsed as the expected JSON object."""


@dataclass(frozen=True)
class LLMResult:
    """Outcome of one Claude call, including token usage and cost."""

    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    model: str
    stop_reason: str | None = None


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse the first JSON object found in ``text``.

    Handles Markdown code fences and leading/trailing prose. Raises
    :class:`LLMOutputError` when no object can be decoded.
    """
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise LLMOutputError(f"Model output is not a JSON object: {text[:200]!r}")


class ClaudeClient:
    """Small facade over ``anthropic.AsyncAnthropic``.

    Args:
        api_key: Anthropic API key. ``None`` disables the client (``enabled`` is False).
        model: Dated model ID.
        timeout_seconds: Per-request timeout passed to the SDK.
        max_retries: Total attempts (including the first) for retryable errors.
        max_tokens: Default completion budget.
    """

    def __init__(
        self,
        api_key: str | None,
        model: str,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        max_tokens: int = 1200,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.max_tokens = max_tokens
        self._client: anthropic.AsyncAnthropic | None = (
            anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_seconds, max_retries=0)
            if api_key
            else None
        )

    @property
    def enabled(self) -> bool:
        """True when an API key was provided."""
        return self._client is not None

    async def complete(
        self, system: str, user: str, *, max_tokens: int | None = None, temperature: float = 0.0
    ) -> LLMResult:
        """Send one system+user prompt and return the text answer with usage.

        Raises:
            RuntimeError: If the client is disabled (no API key).
            anthropic.APIError: After ``max_retries`` failed attempts on retryable errors,
                or immediately on non-retryable ones (4xx other than 429).
        """
        if self._client is None:
            raise RuntimeError("ClaudeClient is disabled: no ANTHROPIC_API_KEY configured")
        budget = max_tokens or self.max_tokens
        t0 = time.perf_counter()
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(RETRYABLE_ERRORS),
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        ):
            with attempt:
                if attempt.retry_state.attempt_number > 1:
                    logger.warning(
                        "llm retry attempt={} model={}",
                        attempt.retry_state.attempt_number,
                        self.model,
                    )
                response = await self._client.messages.create(
                    model=self.model,
                    max_tokens=budget,
                    temperature=temperature,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
        text = "".join(
            getattr(block, "text", "") for block in response.content if block.type == "text"
        )
        usage = response.usage
        result = LLMResult(
            text=text,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=estimate_cost_usd(self.model, usage.input_tokens, usage.output_tokens),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            model=self.model,
            stop_reason=response.stop_reason,
        )
        if result.stop_reason == "max_tokens":
            logger.warning("llm output truncated at max_tokens={} model={}", budget, self.model)
        logger.debug(
            "llm call model={} in={} out={} cost_usd={} latency_ms={}",
            self.model,
            result.input_tokens,
            result.output_tokens,
            result.cost_usd,
            result.latency_ms,
        )
        return result

    async def complete_json(
        self, system: str, user: str, *, max_tokens: int | None = None
    ) -> tuple[dict[str, Any], LLMResult]:
        """Like :meth:`complete` but parse the answer as a JSON object.

        Raises:
            LLMOutputError: When the answer is not a JSON object.
        """
        result = await self.complete(system, user, max_tokens=max_tokens)
        return extract_json_object(result.text), result
