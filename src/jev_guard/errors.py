"""Exception hierarchy. Every error carries a one-line ``hint`` telling the user what to do next."""

from __future__ import annotations


class JevGuardError(Exception):
    """Base class for every error jev-guard raises."""

    default_hint = "See https://github.com/rudra72r/jev-guard#faq"

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint or self.default_hint

    def __str__(self) -> str:
        return f"{self.args[0]}\n  next step: {self.hint}"


class ConfigurationError(JevGuardError):
    """Something about the local setup is wrong: missing API key, bad env var, and so on."""

    default_hint = "Copy .env.example to .env and fill in TYPESAFE_API_KEY."


class PolicyError(JevGuardError):
    """A policy name, override, or definition is invalid."""

    default_hint = "Run `jev-guard policy list` to see the builtin policies."


class JevAPIError(JevGuardError):
    """The Jev API call failed. Checks raise instead of silently allowing or blocking."""

    default_hint = "Retry, or catch JevAPIError and decide whether your app fails open or closed."


class JevAuthenticationError(JevAPIError):
    """The API key was rejected."""

    default_hint = "Check TYPESAFE_API_KEY; create a key at https://console.typesafe.ai/keys"


class JevRateLimitError(JevAPIError):
    """Jev returned 429 after the SDK's own retries were exhausted."""

    default_hint = "Slow down (limit is 1,200 requests/min) or batch questions into fewer checks."


class JevUnavailableError(JevAPIError):
    """Jev timed out, was unreachable, or reported it is overloaded."""

    default_hint = "Jev is unreachable or overloaded; retry with backoff."
