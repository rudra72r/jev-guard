"""Builds the Jev request for an input check (runs before the expensive LLM call)."""

from __future__ import annotations

from typing import Any


def input_state(user_message: str) -> dict[str, Any]:
    """The state Jev sees. Question instructions refer to these field names."""
    return {"user_message": user_message}
