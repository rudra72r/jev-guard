"""JevClient against the real SDK, with HTTP served by a mock transport (no network)."""

from __future__ import annotations

import json

import httpx2
import pytest
from typesafe_sdk import RetryPolicy

import jev_guard.guard as guard_module
from jev_guard import ConfigurationError, Guard
from jev_guard.client import JevClient
from jev_guard.errors import (
    JevAPIError,
    JevAuthenticationError,
    JevRateLimitError,
    JevUnavailableError,
)
from jev_guard.policies.general import INTENT, MATCHES_USER_INTENT, PROMPT_INJECTION

FAKE_KEY = "sk-test-not-a-real-key"

QUESTIONS = {
    "inj": PROMPT_INJECTION.to_wire(),
    "intent": INTENT.to_wire(),
    "fit": MATCHES_USER_INTENT.to_wire(),
}

OK_BODY = {
    "model": "jev-1.13.0",
    "answers": {
        "inj": {"type": "noul", "noul": 0.93},
        "intent": {
            "type": "choice",
            "choice": "malicious",
            "confidence": 0.88,
            "probabilities": {"benign": 0.02, "borderline": 0.10, "malicious": 0.88},
        },
        "fit": {
            "type": "score",
            "score": 2.4,
            "confidence": 0.7,
            "legend": {"0": "a", "1": "b", "2": "c", "3": "d"},
            "probabilities": {"0": 0.0, "1": 0.1, "2": 0.4, "3": 0.5},
        },
    },
    "usage": {"input_tokens": 321, "output_tokens": 9},
}


def handler_for(status: int, body: object, seen: list[httpx2.Request] | None = None):
    def handle(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        return httpx2.Response(status, json=body, headers={"retry-after": "0"})

    return handle


def sync_client(status: int = 200, body: object = OK_BODY, seen=None) -> JevClient:
    return JevClient(
        api_key=FAKE_KEY, transport=httpx2.MockTransport(handler_for(status, body, seen))
    )


def test_sync_request_and_response_mapping():
    seen: list[httpx2.Request] = []
    client = sync_client(seen=seen)
    result = client.evaluate({"user_message": "hi"}, QUESTIONS)

    sent = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/systemone"
    assert seen[0].headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert sent["state"] == {"user_message": "hi"}
    assert sent["questions"]["inj"]["type"] == "noul"
    assert sent["questions"]["fit"]["criteria"][0].startswith("Does not address")

    assert result.model == "jev-1.13.0"
    assert result.input_tokens == 321
    assert result.latency_ms >= 0
    assert result.answers["inj"].noul == 0.93
    assert result.answers["inj"].confidence is None
    assert result.answers["intent"].choice == "malicious"
    assert result.answers["fit"].score == 2.4
    assert result.answers["fit"].probabilities == {"0": 0.0, "1": 0.1, "2": 0.4, "3": 0.5}
    client.close()
    client.close()  # idempotent


async def test_async_request():
    client = JevClient(
        api_key=FAKE_KEY,
        async_transport=httpx2.MockTransport(handler_for(200, OK_BODY)),
    )
    result = await client.aevaluate({"user_message": "hi"}, QUESTIONS)
    assert result.answers["intent"].confidence == 0.88


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, JevAuthenticationError),
        (403, JevAuthenticationError),
        (429, JevRateLimitError),
        (529, JevUnavailableError),
        (422, JevAPIError),
    ],
)
def test_http_errors_are_translated(status, error):
    client = JevClient(
        api_key=FAKE_KEY,
        retry=RetryPolicy(max_retries=0),  # keep the test instant
        transport=httpx2.MockTransport(handler_for(status, {"error": {"message": "nope"}})),
    )
    with pytest.raises(error) as info:
        client.evaluate({"user_message": "hi"}, QUESTIONS)
    assert "next step:" in str(info.value)


def test_missing_key_fails_at_first_check_not_construction(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(guard_module, "_backend_factory", JevClient)
    guard = Guard()  # constructing never needs a key
    assert guard.policy.name == "general"
    with pytest.raises(ConfigurationError, match="TYPESAFE_API_KEY is not set") as info:
        guard.check_input("hello")
    assert "console.typesafe.ai/keys" in info.value.hint


async def test_missing_key_async(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "   ")
    with pytest.raises(ConfigurationError):
        await JevClient().aevaluate({"user_message": "x"}, QUESTIONS)


def test_malformed_key_is_a_configuration_error():
    with pytest.raises(ConfigurationError, match="Jev request failed"):
        JevClient(api_key="sk bad key with spaces").evaluate({"user_message": "x"}, QUESTIONS)


def test_malformed_key_async_is_a_configuration_error():
    with pytest.raises(ConfigurationError):
        JevClient(api_key="sk bad\x00key")._async_client()
