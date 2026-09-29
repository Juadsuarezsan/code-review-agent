"""Token pricing used to compute ``cost_usd`` per request.

Prices are USD per million tokens, taken from Anthropic's public price list at the
time the model was pinned. Update this table when the pinned model changes.
"""

from __future__ import annotations

from src.config import PINNED_MODEL

#: (input_usd_per_mtok, output_usd_per_mtok)
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    PINNED_MODEL: (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return the USD cost of one call.

    Args:
        model: Model ID used for the call.
        input_tokens: Prompt tokens reported by the API.
        output_tokens: Completion tokens reported by the API.

    Returns:
        Cost in USD rounded to 6 decimals. Unknown models are priced at the
        pinned model's rate so that cost is never silently reported as zero.
    """
    price_in, price_out = PRICES_PER_MTOK.get(model, PRICES_PER_MTOK[PINNED_MODEL])
    cost = (input_tokens * price_in + output_tokens * price_out) / 1_000_000
    return round(cost, 6)
