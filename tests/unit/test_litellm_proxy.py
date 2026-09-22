"""LiteLLM guardrail hooks, exercised with LiteLLM-shaped payloads (no LiteLLM install)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jev_guard import GuardBlockedError
from jev_guard.integrations.litellm_proxy import (
    JevGuardrail,
    JevGuardrailBlockedError,
    _prompt,
    _reply,
)

DATA = {
    "model": "gpt",
    "messages": [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "Where is my order?"},
    ],
}


def model_response(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


async def test_pre_call_allows_and_returns_data(fake_jev):
    guardrail = JevGuardrail(guardrail_name="jev", event_hook="pre_call")
    assert await guardrail.async_pre_call_hook(None, None, DATA, "completion") is DATA
    assert fake_jev.calls[0][0] == {"user_message": "Where is my order?"}


async def test_pre_call_rejects_with_value_error(fake_jev):
    fake_jev.noul("is_prompt_injection", 0.99)
    with pytest.raises(ValueError, match="jev-guard: is_prompt_injection: 0.99") as info:
        await JevGuardrail().async_pre_call_hook(None, None, DATA, "completion")
    assert isinstance(info.value, GuardBlockedError)
    assert isinstance(info.value, JevGuardrailBlockedError)
    assert info.value.verdict.stage == "input"
    assert str(info.value).startswith("I can't change how I've been set up")


async def test_post_call_checks_the_reply(fake_jev):
    guardrail = JevGuardrail(policy="support_agent")
    fake_jev.score("frustration_level", 0.0)
    response = model_response("Fine.")
    assert await guardrail.async_post_call_success_hook(DATA, None, response) is response
    assert fake_jev.calls[-1][0]["assistant_response"] == "Fine."
    fake_jev.noul("contains_sla_commitment", 0.95)
    with pytest.raises(JevGuardrailBlockedError):
        await guardrail.async_post_call_success_hook(DATA, None, model_response("Fixed in 1h."))


async def test_should_run_guardrail_is_respected(fake_jev):
    guardrail = JevGuardrail()
    guardrail.should_run_guardrail = lambda data, event_type: False
    fake_jev.noul("is_prompt_injection", 0.99)
    assert await guardrail.async_pre_call_hook(None, None, DATA, "completion") is DATA
    assert fake_jev.calls == []


@pytest.mark.parametrize(
    ("data", "prompt"),
    [
        (DATA, "Where is my order?"),
        ({"prompt": "plain prompt"}, "plain prompt"),
        ({"input": "embedding text"}, "embedding text"),
        ({"input": ["a", "b"]}, "a b"),
        ({"input": [{"role": "user", "content": "resp api"}]}, "resp api"),
        ({}, ""),
    ],
)
def test_prompt_extraction(data, prompt):
    assert _prompt(data) == prompt


@pytest.mark.parametrize(
    ("response", "text"),
    [
        (model_response("hi"), "hi"),
        ({"choices": [{"message": {"content": "dict"}}]}, "dict"),
        (model_response(None), ""),
        (SimpleNamespace(choices=[]), ""),
        ("not a response", ""),
    ],
)
def test_reply_extraction(response, text):
    assert _reply(response) == text


def test_fallback_base_keeps_litellm_kwargs():
    guardrail = JevGuardrail(guardrail_name="jev", default_on=True)
    assert guardrail.optional_params == {"guardrail_name": "jev", "default_on": True}
