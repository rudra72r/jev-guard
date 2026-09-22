"""Anthropic integration: the ``@guarded`` decorator and ``wrap_anthropic`` for existing clients.

    from anthropic import Anthropic
    from jev_guard.integrations.anthropic_sdk import wrap_anthropic

    client = wrap_anthropic(Anthropic(), policy="writing_app")
    client.messages.create(model=..., max_tokens=..., messages=[...])  # GuardBlockedError on block

Covers ``messages.create`` on ``Anthropic`` and ``AsyncAnthropic``, including ``stream=True``
(events come back output-checked; a block raises ``GuardBlockedError`` mid-iteration). The
``messages.stream()`` helper is not wrapped; use ``create(stream=True)`` or
``Guard.astream_check``.
"""

from __future__ import annotations

from typing import Any, TypeVar

from jev_guard.guard import Guard
from jev_guard.integrations._common import guarded, last_user_text, patch_create, text_of
from jev_guard.policies.base import Policy

__all__ = ["guarded", "wrap_anthropic"]

ClientT = TypeVar("ClientT")


def wrap_anthropic(client: ClientT, policy: str | Policy | None = None) -> ClientT:
    """Patch an Anthropic client in place so every call is checked. Returns the same client."""
    messages = getattr(client, "messages", None)
    if messages is None or not hasattr(messages, "create"):
        raise TypeError("wrap_anthropic expects an Anthropic or AsyncAnthropic client")
    patch_create(messages, Guard(policy=policy), _prompt, _reply)
    return client


def _prompt(kwargs: dict[str, Any]) -> str:
    return last_user_text(kwargs.get("messages") or [])


def _reply(response: Any) -> str:
    return text_of(getattr(response, "content", None) or [])
