"""Try backends in order: e.g. Jev first, then a local model when Jev is down or throttled.

A backend is skipped when it raises ``JevAPIError`` (unreachable, overloaded, rate-limited,
rejected key) or ``ConfigurationError`` (no key configured). So ``jev,local`` works fully
offline when no key is set, and keeps checking through a Jev outage. Each fallback is
logged as a warning, and the verdict's model (``jev.model`` in telemetry) shows which
backend actually answered.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from jev_guard.client import JevBackend, JevResult, WireQuestions
from jev_guard.errors import ConfigurationError, JevAPIError, JevGuardError

logger = logging.getLogger("jev_guard")
_FALLBACK_ERRORS = (JevAPIError, ConfigurationError)


class FallbackBackend:
    def __init__(self, *backends: JevBackend) -> None:
        if not backends:
            raise ValueError("FallbackBackend needs at least one backend")
        self.backends = backends

    @property
    def name(self) -> str:
        return "→".join(str(getattr(b, "name", "backend")) for b in self.backends)

    @property
    def needs_typesafe_key(self) -> bool:
        return all(getattr(b, "needs_typesafe_key", True) for b in self.backends)

    @property
    def input_price_per_million(self) -> float:
        return float(getattr(self.backends[0], "input_price_per_million", 0.042))

    def _log(self, backend: JevBackend, err: JevGuardError) -> None:
        logger.warning(
            "jev-guard: backend %s failed (%s); falling back",
            getattr(backend, "name", "backend"),
            err.args[0],
        )

    def evaluate(self, state: Mapping[str, Any], questions: WireQuestions) -> JevResult:
        last: JevGuardError | None = None
        for backend in self.backends:
            try:
                return backend.evaluate(state, questions)
            except _FALLBACK_ERRORS as err:
                self._log(backend, err)
                last = err
        assert last is not None
        raise last

    async def aevaluate(self, state: Mapping[str, Any], questions: WireQuestions) -> JevResult:
        last: JevGuardError | None = None
        for backend in self.backends:
            try:
                return await backend.aevaluate(state, questions)
            except _FALLBACK_ERRORS as err:
                self._log(backend, err)
                last = err
        assert last is not None
        raise last

    def close(self) -> None:
        for backend in self.backends:
            backend.close()
