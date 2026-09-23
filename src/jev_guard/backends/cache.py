"""An in-memory LRU cache in front of any backend.

Identical checks (same text, same questions, same backend) return the stored answers
instantly and cost nothing. The key is a SHA-256 of the request, so the cache holds answers,
never the checked text. Entries expire after ``ttl_seconds``.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from jev_guard.client import JevBackend, JevResult, WireQuestions


class CachingBackend:
    """Wraps ``inner``; a hit returns ``latency_ms=0`` and ``cost_usd=0`` and marks the model."""

    def __init__(self, inner: JevBackend, maxsize: int = 1024, ttl_seconds: float = 3600.0) -> None:
        self.inner = inner
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self.hits = 0
        self.misses = 0
        self._entries: OrderedDict[str, tuple[float, JevResult]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return str(getattr(self.inner, "name", "backend"))  # caching is an implementation detail

    @property
    def needs_typesafe_key(self) -> bool:
        return bool(getattr(self.inner, "needs_typesafe_key", True))

    @property
    def input_price_per_million(self) -> float:
        return float(getattr(self.inner, "input_price_per_million", 0.042))

    def _key(self, state: Mapping[str, Any], questions: WireQuestions) -> str:
        payload = json.dumps(
            [getattr(self.inner, "name", ""), state, questions], sort_keys=True, default=str
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _get(self, key: str) -> JevResult | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry[0] < time.monotonic():
                if entry is not None:
                    del self._entries[key]
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            cached = entry[1]
        return JevResult(
            answers=cached.answers,
            input_tokens=0,
            model=f"{cached.model} (cached)",
            latency_ms=0.0,
            cost_usd=0.0,
        )

    def _put(self, key: str, result: JevResult) -> None:
        if self.maxsize <= 0:
            return
        with self._lock:
            self._entries[key] = (time.monotonic() + self.ttl_seconds, result)
            self._entries.move_to_end(key)
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)

    def evaluate(self, state: Mapping[str, Any], questions: WireQuestions) -> JevResult:
        key = self._key(state, questions)
        cached = self._get(key)
        if cached is not None:
            return cached
        result = self.inner.evaluate(state, questions)
        self._put(key, result)
        return result

    async def aevaluate(self, state: Mapping[str, Any], questions: WireQuestions) -> JevResult:
        key = self._key(state, questions)
        cached = self._get(key)
        if cached is not None:
            return cached
        result = await self.inner.aevaluate(state, questions)
        self._put(key, result)
        return result

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def close(self) -> None:
        self.inner.close()
