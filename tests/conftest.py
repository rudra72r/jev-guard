"""Shared fixtures. Unit tests never touch the network: Guard runs against FakeJevClient."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

import jev_guard.guard as guard_module
from jev_guard.client import JevResult
from jev_guard.types import JevAnswer


class FakeJevClient:
    """Programmable stand-in for Jev.

    Unset questions get a "clean" answer: noul 0.01, the first choice label at 0.97,
    the top score level. Set specific answers with ``fake.answers["name"] = JevAnswer(...)``
    or the ``noul`` / ``choice`` / ``score`` helpers.
    """

    def __init__(self) -> None:
        self.answers: dict[str, JevAnswer] = {}
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.input_tokens: int | None = 250
        self.model = "jev-1.13.0"
        self.latency_ms = 120.0
        self.error: Exception | None = None
        self.closed = False

    def noul(self, name: str, value: float) -> None:
        self.answers[name] = JevAnswer(type="noul", noul=value)

    def choice(self, name: str, label: str, confidence: float, **others: float) -> None:
        probabilities = {label: confidence, **others}
        self.answers[name] = JevAnswer(
            type="choice", choice=label, confidence=confidence, probabilities=probabilities
        )

    def score(self, name: str, value: float, confidence: float = 0.9) -> None:
        self.answers[name] = JevAnswer(type="score", score=value, confidence=confidence)

    def _clean(self, question: Mapping[str, Any]) -> JevAnswer:
        kind = question["type"]
        if kind == "noul":
            return JevAnswer(type="noul", noul=0.01)
        if kind == "choice":
            first = next(iter(question["criteria"]))
            return JevAnswer(
                type="choice", choice=first, confidence=0.97, probabilities={first: 0.97}
            )
        top = len(question["criteria"]) - 1
        return JevAnswer(type="score", score=float(top), confidence=0.95)

    def evaluate(self, state: Mapping[str, Any], questions: Mapping[str, Any]) -> JevResult:
        self.calls.append((dict(state), {k: dict(v) for k, v in questions.items()}))
        if self.error is not None:
            raise self.error
        answers = {name: self.answers.get(name) or self._clean(q) for name, q in questions.items()}
        return JevResult(answers, self.input_tokens, self.model, self.latency_ms)

    async def aevaluate(self, state: Mapping[str, Any], questions: Mapping[str, Any]) -> JevResult:
        return self.evaluate(state, questions)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_jev(monkeypatch: pytest.MonkeyPatch) -> FakeJevClient:
    fake = FakeJevClient()
    monkeypatch.setattr(guard_module, "_backend_factory", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Keep unit tests independent of the developer's shell environment."""
    if request.node.get_closest_marker("integration"):
        return
    for name in (
        "JEV_GUARD_DEFAULT_POLICY",
        "JEV_GUARD_BACKEND",
        "JEV_GUARD_CACHE_SIZE",
        "JEV_GUARD_MAX_RPM",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _reset_backend():
    """No test may leak a process-wide backend into another."""
    from jev_guard import backends  # noqa: PLC0415

    backends.set_backend(None)
    yield
    backends.set_backend(None)
