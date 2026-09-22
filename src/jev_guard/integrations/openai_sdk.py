"""OpenAI integration: the ``@guarded`` decorator and ``wrap_openai`` for existing clients.

    from openai import OpenAI
    from jev_guard.integrations.openai_sdk import wrap_openai

    client = wrap_openai(OpenAI(), policy="support_agent")
    client.chat.completions.create(model=..., messages=[...])  # raises GuardBlockedError on block

Covers ``chat.completions.create`` and ``responses.create`` on both ``OpenAI`` and
``AsyncOpenAI`` (openai>=1.0). With ``stream=True`` you get the SDK's chunk objects back,
output-checked with the policy's streaming strategy (buffer-and-check by default); a block
raises ``GuardBlockedError`` mid-iteration.
"""

from __future__ import annotations

from typing import Any, TypeVar

from jev_guard.guard import Guard
from jev_guard.integrations._common import guarded, last_user_text, patch_create, text_of
from jev_guard.policies.base import Policy

__all__ = ["guarded", "wrap_openai"]

ClientT = TypeVar("ClientT")


def wrap_openai(client: ClientT, policy: str | Policy | None = None) -> ClientT:
    """Patch an OpenAI client in place so every call is checked. Returns the same client."""
    guard = Guard(policy=policy)
    chat = getattr(getattr(client, "chat", None), "completions", None)
    if chat is not None:
        patch_create(chat, guard, _chat_prompt, _chat_reply)
    responses = getattr(client, "responses", None)
    if responses is not None and hasattr(responses, "create"):
        patch_create(responses, guard, _responses_prompt, _responses_reply)
    return client


def _chat_prompt(kwargs: dict[str, Any]) -> str:
    return last_user_text(kwargs.get("messages") or [])


def _chat_reply(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    return text_of(getattr(choices[0].message, "content", None) or "")


def _responses_prompt(kwargs: dict[str, Any]) -> str:
    request = kwargs.get("input")
    if isinstance(request, str):
        return request
    return last_user_text(request or [])


def _responses_reply(response: Any) -> str:
    text = getattr(response, "output_text", None)
    return text if isinstance(text, str) else ""
