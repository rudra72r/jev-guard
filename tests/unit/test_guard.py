"""Guard behaviour: async parity, skips, cost, errors, lifecycle, Verdict serialization."""

from __future__ import annotations

import json

import pytest

from jev_guard import Guard, JevAPIError, Policy, Verdict
from jev_guard.cost import estimate_cost_usd, estimate_tokens


async def test_async_checks_match_sync(fake_jev):
    guard = Guard()
    fake_jev.noul("is_prompt_injection", 0.99)
    sync_v = guard.check_input("x")
    async_v = await guard.acheck_input("x")
    assert sync_v.model_dump(exclude={"latency_ms"}) == async_v.model_dump(exclude={"latency_ms"})

    fake_jev.noul("contains_pii", 0.9)
    assert (await guard.acheck_output("q", "a", context=["doc"])).action == "review"
    assert fake_jev.calls[-1][0]["context"] == ["doc"]


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
async def test_blank_text_skips_jev(fake_jev, text):
    guard = Guard()
    for verdict in (
        guard.check_input(text),
        guard.check_output("q", text),
        await guard.acheck_input(text),
        await guard.acheck_output("q", text),
    ):
        assert verdict.action == "allow"
        assert verdict.estimated_cost_usd == 0.0
        assert verdict.reasons == ["skipped: nothing to check (empty text)"]
    assert fake_jev.calls == []


async def test_stage_without_questions_skips_jev(fake_jev):
    guard = Guard(policy=Policy(name="input_only"))
    assert guard.check_output("q", "a").reasons == ["skipped: policy has no output questions"]
    assert (await guard.acheck_input("q")).reasons == ["skipped: policy has no input questions"]
    assert fake_jev.calls == []


def test_non_string_input_is_a_type_error(fake_jev):
    with pytest.raises(TypeError, match="expected str"):
        Guard().check_input(None)  # type: ignore[arg-type]


def test_cost_uses_reported_tokens(fake_jev):
    fake_jev.input_tokens = 1_000
    v = Guard().check_input("hello")
    assert v.input_tokens_used == 1_000
    assert v.estimated_cost_usd == pytest.approx(0.000042)
    assert v.latency_ms == 120.0


def test_cost_falls_back_to_estimate_when_usage_missing(fake_jev):
    fake_jev.input_tokens = None
    v = Guard().check_input("hello")
    assert v.input_tokens_used > 0
    assert v.estimated_cost_usd > 0


def test_cost_helpers():
    assert estimate_cost_usd(1_000_000, "jev-1.13.0") == pytest.approx(0.042)
    assert estimate_cost_usd(1_000_000, "jev-9.9.9") == pytest.approx(0.042)
    assert estimate_tokens("abcd" * 10) == 10
    assert estimate_tokens({"a": 1}) == 2


def test_api_errors_propagate(fake_jev):
    fake_jev.error = JevAPIError("boom")
    with pytest.raises(JevAPIError, match="boom"):
        Guard().check_input("x")


def test_error_message_includes_next_step_hint():
    err = JevAPIError("boom")
    assert "next step:" in str(err)
    assert JevAPIError("boom", hint="do X").hint == "do X"


def test_context_manager_closes_backend(fake_jev):
    with Guard(policy="general") as g:
        g.check_input("x")
    assert fake_jev.closed


def test_context_manager_without_calls_creates_no_backend(fake_jev):
    with Guard():
        pass
    assert not fake_jev.closed


def test_policy_must_be_name_or_policy():
    with pytest.raises(Exception, match="policy must be"):
        Guard(policy=123)  # type: ignore[arg-type]


def test_verdict_roundtrips_through_json(fake_jev):
    fake_jev.noul("is_prompt_injection", 0.97)
    fake_jev.choice("intent", "malicious", 0.9, benign=0.1)
    v = Guard().check_input("x")
    dumped = v.model_dump()
    assert dumped["blocked"] is True
    text = json.dumps(dumped)
    restored = Verdict.model_validate_json(text)
    assert restored == v
    assert restored.raw_answers["intent"].probabilities == {"malicious": 0.9, "benign": 0.1}


def test_verdict_is_frozen(fake_jev):
    v = Guard().check_input("x")
    with pytest.raises(Exception, match="frozen"):
        v.action = "block"  # type: ignore[misc]
