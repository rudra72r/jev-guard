"""Client-side rate limiting, so a busy process queues requests instead of hitting 429s.

TypeSafe's published limit is 1,200 requests per minute. ``RateLimitedBackend`` spaces
calls with the generic cell rate algorithm (GCRA): requests up to ``burst`` go straight
through, after which each waits its turn. Sync and async callers share one budget, and the
wait happens outside the lock, so waiting never blocks other threads' bookkeeping. The limit
is per process; several processes sharing one key need a lower setting each.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Mapping
from typing import Any

from jev_guard.client import JevBackend, JevResult, WireQuestions

TYPESAFE_REQUESTS_PER_MINUTE = 1200
DEFAULT_REQUESTS_PER_MINUTE = 1100  # a little headroom under TypeSafe's limit


class RateLimitedBackend:
    def __init__(
        self,
        inner: JevBackend,
        requests_per_minute: float = DEFAULT_REQUESTS_PER_MINUTE,
        burst: int = 20,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self.inner = inner
        self.interval = 60.0 / requests_per_minute
        self.tolerance = self.interval * max(0, burst - 1)
        self._tat = 0.0  # theoretical arrival time of the next request
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return str(getattr(self.inner, "name", "backend"))

    @property
    def needs_typesafe_key(self) -> bool:
        return bool(getattr(self.inner, "needs_typesafe_key", True))

    @property
    def input_price_per_million(self) -> float:
        return float(getattr(self.inner, "input_price_per_million", 0.042))

    def reserve(self) -> float:
        """Claim the next slot; returns how many seconds to wait before using it."""
        with self._lock:
            now = time.monotonic()
            tat = max(self._tat, now)
            wait = max(0.0, tat - self.tolerance - now)
            self._tat = tat + self.interval
            return wait

    def evaluate(self, state: Mapping[str, Any], questions: WireQuestions) -> JevResult:
        wait = self.reserve()
        if wait:
            time.sleep(wait)
        return self.inner.evaluate(state, questions)

    async def aevaluate(self, state: Mapping[str, Any], questions: WireQuestions) -> JevResult:
        wait = self.reserve()
        if wait:
            await asyncio.sleep(wait)
        return await self.inner.aevaluate(state, questions)

    def close(self) -> None:
        self.inner.close()
