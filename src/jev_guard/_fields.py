"""Read a field out of an object jev-guard didn't define.

Every SDK models the same message two ways — OpenAI returns pydantic objects but accepts
dicts, LangChain uses attributes, a proxy hands over raw JSON — and jev-guard has to read
``content`` or ``role`` from all of them without importing any of them. One helper, used by
the stream readers, the integrations and the conversation checker.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def field(obj: Any, key: str) -> Any:
    """``obj[key]`` for mappings, ``obj.key`` for objects, None when absent or ``obj`` is None."""
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)
