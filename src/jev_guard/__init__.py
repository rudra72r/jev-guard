"""jev-guard: fast, cheap guardrails for LLM apps, powered by TypeSafe's Jev model."""

from jev_guard.errors import (
    ConfigurationError,
    GuardBlockedError,
    JevAPIError,
    JevGuardError,
    PolicyError,
)
from jev_guard.guard import Guard
from jev_guard.guards.streaming import StreamCut
from jev_guard.policies import Policy
from jev_guard.redact import aredact, redact
from jev_guard.types import Action, JevAnswer, Verdict

__version__ = "0.1.0"

__all__ = [
    "Action",
    "ConfigurationError",
    "Guard",
    "GuardBlockedError",
    "JevAPIError",
    "JevAnswer",
    "JevGuardError",
    "Policy",
    "PolicyError",
    "StreamCut",
    "Verdict",
    "__version__",
    "aredact",
    "redact",
]
