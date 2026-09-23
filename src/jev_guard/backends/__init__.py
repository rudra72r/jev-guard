"""Choose what answers the policies' questions: Jev, a local model, an LLM judge, or a chain.

Every ``Guard`` (and ``ToolGuard``, wrapper, hook, CLI command) uses the process-wide
backend, so switching is one line and no other code changes:

    from jev_guard import backends

    backends.set_backend("jev")                 # default: TypeSafe's Jev
    backends.set_backend("local")               # offline: local transformer models
    backends.set_backend("ollama:llama3.1")     # a local LLM through Ollama
    backends.set_backend("jev,local")           # Jev, falling back to local when it fails

Or without code: ``JEV_GUARD_BACKEND=jev,local``, or ``jev-guard --backend local ...``.

Spec strings:

| spec | backend |
|---|---|
| ``jev`` / ``jev:MODEL`` | TypeSafe Jev (``TYPESAFE_API_KEY``) |
| ``local`` / ``local:NLI_MODEL`` | local models (``pip install "jev-guard[local]"``) |
| ``openai:MODEL`` | OpenAI as judge (``OPENAI_API_KEY``) |
| ``ollama:MODEL`` | Ollama at ``http://localhost:11434/v1`` |
| ``openai-compatible:MODEL@BASE_URL`` | vLLM, LM Studio, llama.cpp, OpenRouter, … |
| ``anthropic:MODEL`` | Claude as judge (``ANTHROPIC_API_KEY``) |
| ``a,b,c`` | try each in order (``FallbackBackend``) |

Every spec gets an in-memory answer cache (``JEV_GUARD_CACHE_SIZE``, default 1024, ``0`` to
disable), and Jev gets a client-side rate limit (``JEV_GUARD_MAX_RPM``, default 1100, under
TypeSafe's 1,200 per minute). Objects passed to ``set_backend`` are used as-is.
"""

from __future__ import annotations

import os
import threading

from jev_guard.backends.cache import CachingBackend
from jev_guard.backends.fallback import FallbackBackend
from jev_guard.backends.judge import AnthropicJudge, OpenAICompatibleJudge
from jev_guard.backends.local import LocalBackend, Specialist
from jev_guard.backends.ratelimit import DEFAULT_REQUESTS_PER_MINUTE, RateLimitedBackend
from jev_guard.client import JevBackend, JevClient
from jev_guard.errors import ConfigurationError

__all__ = [
    "AnthropicJudge",
    "Backend",
    "CachingBackend",
    "FallbackBackend",
    "JevClient",
    "LocalBackend",
    "OpenAICompatibleJudge",
    "RateLimitedBackend",
    "Specialist",
    "from_spec",
    "get_backend",
    "needs_typesafe_key",
    "price_per_million",
    "set_backend",
]

Backend = JevBackend

BACKEND_ENV = "JEV_GUARD_BACKEND"
CACHE_ENV = "JEV_GUARD_CACHE_SIZE"
RPM_ENV = "JEV_GUARD_MAX_RPM"
OLLAMA_URL = "http://localhost:11434/v1"
DEFAULT_CACHE_SIZE = 1024


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as err:
        raise ConfigurationError(f"{name} must be a whole number, got {raw!r}.") from err


def _one(spec: str) -> Backend:
    kind, _, arg = spec.strip().partition(":")
    kind = kind.strip().lower()
    if kind == "jev":
        rpm = _int_env(RPM_ENV, DEFAULT_REQUESTS_PER_MINUTE)
        client = JevClient(model=arg or None)
        return RateLimitedBackend(client, rpm) if rpm > 0 else client
    if kind == "local":
        return LocalBackend(arg) if arg else LocalBackend()
    if kind == "openai" and arg:
        return OpenAICompatibleJudge(arg)
    if kind == "ollama" and arg:
        return OpenAICompatibleJudge(arg, base_url=OLLAMA_URL, api_key="")
    if kind == "openai-compatible" and "@" in arg:
        model, _, base_url = arg.partition("@")
        return OpenAICompatibleJudge(
            model, base_url=base_url, api_key=os.environ.get("JEV_GUARD_JUDGE_API_KEY", "")
        )
    if kind == "anthropic" and arg:
        return AnthropicJudge(arg)
    raise ConfigurationError(
        f"Unknown backend spec {spec!r}.",
        hint="Use jev, local, openai:MODEL, ollama:MODEL, anthropic:MODEL, "
        "openai-compatible:MODEL@URL, or a comma-separated chain like jev,local.",
    )


def from_spec(spec: str, *, cache_size: int | None = None) -> Backend:
    """Build a backend from a spec string (see the module docstring)."""
    parts = [part for part in spec.split(",") if part.strip()]
    if not parts:
        raise ConfigurationError("Empty backend spec.", hint="Use jev, local, or jev,local.")
    chain = [_one(part) for part in parts]
    backend: Backend = chain[0] if len(chain) == 1 else FallbackBackend(*chain)
    size = _int_env(CACHE_ENV, DEFAULT_CACHE_SIZE) if cache_size is None else cache_size
    return CachingBackend(backend, maxsize=size) if size > 0 else backend


class _State:
    def __init__(self) -> None:
        self.backend: Backend | None = None
        self.lock = threading.Lock()


_state = _State()


def set_backend(backend: Backend | str | None) -> None:
    """Use this backend for every check in the process. ``None`` goes back to the default."""
    resolved = from_spec(backend) if isinstance(backend, str) else backend
    with _state.lock:
        _state.backend = resolved


def get_backend() -> Backend:
    """The process-wide backend, built once from ``JEV_GUARD_BACKEND`` (default ``jev``)."""
    with _state.lock:
        if _state.backend is None:
            _state.backend = from_spec(os.environ.get(BACKEND_ENV, "").strip() or "jev")
        return _state.backend


def needs_typesafe_key(backend: Backend) -> bool:
    """Whether this backend can't work at all without TYPESAFE_API_KEY."""
    return bool(getattr(backend, "needs_typesafe_key", True))


def price_per_million(backend: Backend) -> float:
    """Input price used for cost estimates (0 for local models)."""
    return float(getattr(backend, "input_price_per_million", 0.042))
