"""Live-Jev tests. These cost real money, so they are double-gated and budget-capped.

They run only when BOTH are set:
  TYPESAFE_API_KEY=sk-...
  JEV_GUARD_ALLOW_LIVE=1

A hard token budget stops the session from spending more than MAX_LIVE_TOKENS
(200k tokens ~= $0.008 at $0.042 per 1M input tokens).
"""

from __future__ import annotations

import os

import pytest

from jev_guard import Verdict

MAX_LIVE_TOKENS = 200_000
_spent = {"tokens": 0}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    has_key = bool(os.environ.get("TYPESAFE_API_KEY", "").strip())
    allowed = os.environ.get("JEV_GUARD_ALLOW_LIVE") == "1"
    if has_key and allowed:
        return
    why = "set TYPESAFE_API_KEY and JEV_GUARD_ALLOW_LIVE=1 to run live Jev tests"
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(pytest.mark.skip(reason=why))


@pytest.fixture
def spend():
    """Call ``spend(verdict)`` after every live check; skips once the budget is used up."""
    if _spent["tokens"] >= MAX_LIVE_TOKENS:
        pytest.skip(f"live token budget of {MAX_LIVE_TOKENS} used up")

    def record(verdict: Verdict) -> Verdict:
        _spent["tokens"] += verdict.input_tokens_used
        return verdict

    return record
