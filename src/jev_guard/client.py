"""Thin sync + async wrapper over the TypeSafe SDK.

Everything above this module speaks ``JevResult`` / ``JevAnswer``, never SDK types, so tests
can swap in a fake backend and an SDK upgrade only touches this file. SDK clients are created
lazily on first use: importing jev-guard or inspecting a policy never needs an API key.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx2
from typesafe_sdk import (
    AsyncTypeSafeClient,
    ChoiceAnswer,
    NoulAnswer,
    RetryPolicy,
    ScoreAnswer,
    SystemOneResponse,
    TypeSafeAPIConnectionError,
    TypeSafeAPITimeoutError,
    TypeSafeAuthenticationError,
    TypeSafeClient,
    TypeSafeError,
    TypeSafeInternalServerError,
    TypeSafePermissionDeniedError,
    TypeSafeRateLimitError,
)

from jev_guard.errors import (
    ConfigurationError,
    JevAPIError,
    JevAuthenticationError,
    JevGuardError,
    JevRateLimitError,
    JevUnavailableError,
)
from jev_guard.types import JevAnswer

API_KEY_ENV = "TYPESAFE_API_KEY"

WireQuestions = Mapping[str, Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class JevResult:
    """One Jev call's answers plus the metadata jev-guard needs for cost and telemetry."""

    answers: dict[str, JevAnswer]
    input_tokens: int | None
    model: str
    latency_ms: float


class JevBackend(Protocol):
    """What a Guard needs from Jev. ``JevClient`` is the real one; tests use a fake."""

    def evaluate(self, state: Mapping[str, Any], questions: WireQuestions) -> JevResult: ...

    async def aevaluate(self, state: Mapping[str, Any], questions: WireQuestions) -> JevResult: ...

    def close(self) -> None: ...


class JevClient:
    """Lazily-constructed sync and async TypeSafe clients sharing one configuration.

    ``api_key`` defaults to ``TYPESAFE_API_KEY``. ``transport``, ``async_transport`` and
    ``retry`` exist for tests.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        transport: httpx2.BaseTransport | None = None,
        async_transport: httpx2.AsyncBaseTransport | None = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._transport = transport
        self._async_transport = async_transport
        self._retry = retry
        self._sync: TypeSafeClient | None = None
        self._async: AsyncTypeSafeClient | None = None
        self._lock = threading.Lock()

    def _resolve_key(self) -> str:
        key = (self._api_key or os.environ.get(API_KEY_ENV, "")).strip()
        if not key:
            raise ConfigurationError(
                f"{API_KEY_ENV} is not set, so jev-guard can't reach Jev.",
                hint=f"Set {API_KEY_ENV} (get a key at https://console.typesafe.ai/keys).",
            )
        return key

    def _sync_client(self) -> TypeSafeClient:
        with self._lock:
            if self._sync is None:
                key = self._resolve_key()
                try:
                    self._sync = TypeSafeClient(
                        api_key=key, model=self._model, retry=self._retry, transport=self._transport
                    )
                except TypeSafeError as err:
                    raise _translate(err) from err
            return self._sync

    def _async_client(self) -> AsyncTypeSafeClient:
        with self._lock:
            if self._async is None:
                key = self._resolve_key()
                try:
                    self._async = AsyncTypeSafeClient(
                        api_key=key,
                        model=self._model,
                        retry=self._retry,
                        transport=self._async_transport,
                    )
                except TypeSafeError as err:
                    raise _translate(err) from err
            return self._async

    def evaluate(self, state: Mapping[str, Any], questions: WireQuestions) -> JevResult:
        client = self._sync_client()
        start = time.perf_counter()
        try:
            response = client.system_one(state=dict(state), questions=_plain(questions))
        except TypeSafeError as err:
            raise _translate(err) from err
        return _to_result(response, (time.perf_counter() - start) * 1000)

    async def aevaluate(self, state: Mapping[str, Any], questions: WireQuestions) -> JevResult:
        client = self._async_client()
        start = time.perf_counter()
        try:
            response = await client.system_one(state=dict(state), questions=_plain(questions))
        except TypeSafeError as err:
            raise _translate(err) from err
        return _to_result(response, (time.perf_counter() - start) * 1000)

    def close(self) -> None:
        with self._lock:
            if self._sync is not None:
                self._sync.close()
                self._sync = None


def _plain(questions: WireQuestions) -> dict[str, Any]:
    return {name: dict(question) for name, question in questions.items()}


def _to_answer(answer: NoulAnswer | ChoiceAnswer | ScoreAnswer) -> JevAnswer:
    if isinstance(answer, NoulAnswer):
        return JevAnswer(type="noul", noul=answer.noul)
    if isinstance(answer, ChoiceAnswer):
        return JevAnswer(
            type="choice",
            choice=answer.choice,
            confidence=answer.confidence,
            probabilities=dict(answer.probabilities),
        )
    return JevAnswer(
        type="score",
        score=answer.score,
        confidence=answer.confidence,
        probabilities={str(level): p for level, p in answer.probabilities.items()},
    )


def _to_result(response: SystemOneResponse, latency_ms: float) -> JevResult:
    return JevResult(
        answers={name: _to_answer(answer) for name, answer in response.answers.items()},
        input_tokens=response.usage.input_tokens,
        model=response.model,
        latency_ms=latency_ms,
    )


def _translate(err: TypeSafeError) -> JevGuardError:
    """Map SDK exceptions onto jev-guard's hierarchy, keeping the SDK message."""
    message = f"Jev request failed: {err}"
    if isinstance(err, TypeSafeAuthenticationError | TypeSafePermissionDeniedError):
        return JevAuthenticationError(message)
    if isinstance(err, TypeSafeRateLimitError):
        return JevRateLimitError(message)
    if isinstance(
        err, TypeSafeAPITimeoutError | TypeSafeAPIConnectionError | TypeSafeInternalServerError
    ):
        return JevUnavailableError(message)
    if "api key" in str(err).lower():
        return ConfigurationError(message, hint=f"Check the format of {API_KEY_ENV}.")
    return JevAPIError(message)
