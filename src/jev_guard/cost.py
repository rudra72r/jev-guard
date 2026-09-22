"""Token counting and dollar estimates for Jev checks.

Prices come from https://docs.typesafe.ai/models (checked 2026-09-22). Output tokens are free,
so only input tokens are billed.
"""

from __future__ import annotations

import json
import math
from typing import Any

# USD per 1M input tokens, keyed by the resolved model name Jev reports back.
PRICE_PER_MILLION_INPUT_USD: dict[str, float] = {
    "jev-1.13.0": 0.042,
}
DEFAULT_PRICE_PER_MILLION_INPUT_USD = 0.042

# Rough English-text ratio; only used when the API doesn't report usage.
_CHARS_PER_TOKEN = 4


def estimate_cost_usd(input_tokens: int, model: str | None = None) -> float:
    """Dollar cost of one Jev call. Unknown models fall back to the current list price."""
    price = PRICE_PER_MILLION_INPUT_USD.get(model or "", DEFAULT_PRICE_PER_MILLION_INPUT_USD)
    return input_tokens * price / 1_000_000


def estimate_tokens(payload: Any) -> int:
    """Approximate token count of a state + questions payload (about 4 characters per token)."""
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    return math.ceil(len(text) / _CHARS_PER_TOKEN)
