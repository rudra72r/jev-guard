"""OpenTelemetry spans for every Jev check. Optional: ``pip install "jev-guard[otel]"``.

    from jev_guard.telemetry import setup_telemetry
    setup_telemetry()

After that, every check that calls Jev emits a ``jev_guard.check`` span with the attributes
in ``SPAN_ATTRIBUTES``; checks skipped without a Jev call (empty text) emit none. Spans go to
whatever tracer provider and exporter the app has configured (Langfuse, Arize, Honeycomb,
Jaeger, Sentry, ...); with none configured, OpenTelemetry drops them at no cost.
``with Guard(...) as g:`` wraps its checks in a parent ``jev_guard.session`` span.

Setting ``JEV_GUARD_TELEMETRY_ENABLED=1`` calls ``setup_telemetry()`` automatically the first
time a ``Guard`` is created.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jev_guard.policies.base import Policy
    from jev_guard.types import GuardStage, Verdict

SPAN_NAME = "jev_guard.check"
SESSION_SPAN_NAME = "jev_guard.session"
ENV_FLAG = "JEV_GUARD_TELEMETRY_ENABLED"
SPAN_ATTRIBUTES = (
    "policy.name",
    "policy.version",
    "guard.stage",
    "verdict.action",
    "verdict.confidence",
    "verdict.blocked",
    "verdict.reasons",
    "latency.ms",
    "tokens.input",
    "cost.usd",
    "jev.model",
)
_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(slots=True)
class _State:
    tracer: Any = None
    auto_attempted: bool = False


_state = _State()


def setup_telemetry(tracer_provider: Any | None = None) -> bool:
    """Start emitting spans. Returns False (with an install hint) if OpenTelemetry is missing.

    ``tracer_provider`` defaults to the global one the app configured.
    """
    try:
        from opentelemetry import trace  # noqa: PLC0415 (optional dependency)
    except ImportError:
        sys.stderr.write(
            'jev-guard telemetry needs OpenTelemetry.\n  next step: pip install "jev-guard[otel]"\n'
        )
        return False
    from jev_guard import __version__  # noqa: PLC0415 (import cycle)

    provider = tracer_provider or trace.get_tracer_provider()
    _state.tracer = provider.get_tracer("jev_guard", __version__)
    return True


def disable_telemetry() -> None:
    """Stop emitting spans (mainly for tests)."""
    _state.tracer = None


def telemetry_enabled() -> bool:
    return _state.tracer is not None


def auto_setup() -> None:
    """Honour JEV_GUARD_TELEMETRY_ENABLED once per process."""
    if _state.auto_attempted:
        return
    _state.auto_attempted = True
    if os.environ.get(ENV_FLAG, "").strip().lower() in _TRUTHY and _state.tracer is None:
        setup_telemetry()


@dataclass(slots=True)
class CheckSpan:
    span: Any

    def record(self, verdict: Verdict, model: str) -> None:
        self.span.set_attributes(
            {
                "verdict.action": verdict.action,
                "verdict.confidence": verdict.confidence,
                "verdict.blocked": verdict.blocked,
                "verdict.reasons": ",".join(verdict.reasons),
                "latency.ms": verdict.latency_ms,
                "tokens.input": verdict.input_tokens_used,
                "cost.usd": verdict.estimated_cost_usd,
                "jev.model": model,
            }
        )


@contextlib.contextmanager
def check_span(policy: Policy, stage: GuardStage) -> Iterator[CheckSpan | None]:
    """A span around one Jev call, or None when telemetry is off. Exceptions are recorded."""
    tracer = _state.tracer
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(
        SPAN_NAME,
        attributes={
            "policy.name": policy.name,
            "policy.version": policy.version,
            "guard.stage": stage,
        },
    ) as span:
        yield CheckSpan(span)


@dataclass(slots=True)
class Session:
    span: Any
    token: Any


def start_session(policy: Policy) -> Session | None:
    tracer = _state.tracer
    if tracer is None:
        return None
    from opentelemetry import context, trace  # noqa: PLC0415 (optional dependency)

    span = tracer.start_span(
        SESSION_SPAN_NAME,
        attributes={"policy.name": policy.name, "policy.version": policy.version},
    )
    return Session(span=span, token=context.attach(trace.set_span_in_context(span)))


def end_session(session: Session | None, error: BaseException | None) -> None:
    if session is None:
        return
    from opentelemetry import context  # noqa: PLC0415 (optional dependency)
    from opentelemetry.trace import Status, StatusCode  # noqa: PLC0415

    context.detach(session.token)
    if error is not None:
        session.span.record_exception(error)
        session.span.set_status(Status(StatusCode.ERROR, str(error)))
    session.span.end()
