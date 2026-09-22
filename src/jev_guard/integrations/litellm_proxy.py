"""LiteLLM proxy integration: a custom guardrail that runs jev-guard on every request.

In the proxy's ``config.yaml``::

    guardrails:
      - guardrail_name: jev-guard-input
        litellm_params:
          guardrail: jev_guard.integrations.litellm_proxy.JevGuardrail
          mode: pre_call        # check the user's message before the model is called
      - guardrail_name: jev-guard-output
        litellm_params:
          guardrail: jev_guard.integrations.litellm_proxy.JevGuardrail
          mode: post_call       # check the reply before it's returned

Pick the policy with ``JEV_GUARD_DEFAULT_POLICY`` (a builtin name or a YAML path) and set
``TYPESAFE_API_KEY`` in the proxy's environment. Blocked requests are rejected with
``JevGuardrailBlockedError`` (a ``ValueError``, which is how LiteLLM guardrails reject), carrying
the verdict and a safe reply. Streaming responses are not output-checked by this hook in
v0.1. Checked against LiteLLM 1.102's ``CustomGuardrail`` interface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jev_guard.errors import GuardBlockedError
from jev_guard.guard import Guard
from jev_guard.integrations._common import last_user_text, text_of
from jev_guard.types import Verdict

if TYPE_CHECKING:

    class _Base:
        def __init__(self, **kwargs: Any) -> None: ...

else:
    try:
        from litellm.integrations.custom_guardrail import CustomGuardrail as _Base
    except ImportError:  # usable (and testable) without LiteLLM installed

        class _Base:
            def __init__(self, **kwargs: Any) -> None:
                self.optional_params = kwargs


__all__ = ["JevGuardrail", "JevGuardrailBlockedError"]


class JevGuardrailBlockedError(GuardBlockedError, ValueError):
    """Raised to reject a request; LiteLLM returns its message to the caller."""

    def __init__(self, verdict: Verdict) -> None:
        super().__init__(verdict)
        reply = verdict.suggested_response or "Blocked by the jev-guard safety policy."
        self.args = (f"{reply} (jev-guard: {'; '.join(verdict.reasons)})",)

    def __str__(self) -> str:
        return str(self.args[0])


def _prompt(data: dict[str, Any]) -> str:
    messages = data.get("messages")
    if messages:
        return last_user_text(messages)
    for key in ("prompt", "input"):
        value = data.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return last_user_text(value) or " ".join(v for v in value if isinstance(v, str))
    return ""


def _reply(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        return ""
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
    content = (
        message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    )
    return text_of(content or "")


class JevGuardrail(_Base):
    """LiteLLM ``CustomGuardrail`` backed by a jev-guard ``Guard``."""

    def __init__(self, policy: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.guard = Guard(policy=policy)

    def _enabled(self, data: dict[str, Any], hook: str) -> bool:
        """Respect LiteLLM's per-request guardrail selection when it's available."""
        should_run = getattr(self, "should_run_guardrail", None)
        if should_run is None:
            return True
        event_type: Any = hook
        try:
            from litellm.types.guardrails import GuardrailEventHooks  # noqa: PLC0415
        except ImportError:
            pass  # GuardrailEventHooks is a str enum, so the plain value compares equal
        else:
            event_type = GuardrailEventHooks(hook)
        return bool(should_run(data=data, event_type=event_type))

    async def async_pre_call_hook(
        self, user_api_key_dict: Any, cache: Any, data: dict[str, Any], call_type: Any
    ) -> dict[str, Any]:
        if self._enabled(data, "pre_call"):
            verdict = await self.guard.acheck_input(_prompt(data))
            if verdict.blocked:
                raise JevGuardrailBlockedError(verdict)
        return data

    async def async_post_call_success_hook(
        self, data: dict[str, Any], user_api_key_dict: Any, response: Any
    ) -> Any:
        if self._enabled(data, "post_call"):
            verdict = await self.guard.acheck_output(_prompt(data), _reply(response))
            if verdict.blocked:
                raise JevGuardrailBlockedError(verdict)
        return response
