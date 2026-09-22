"""Builds the Jev request for an output check (runs after the LLM responds)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

Context = str | Sequence[str] | None


def output_state(user_message: str, llm_response: str, context: Context = None) -> dict[str, Any]:
    """The state Jev sees. ``context`` (retrieved documents) is included only when given."""
    state: dict[str, Any] = {"user_message": user_message, "assistant_response": llm_response}
    if context is not None:
        state["context"] = [context] if isinstance(context, str) else list(context)
    return state
