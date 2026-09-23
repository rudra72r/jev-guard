"""``Guard``: the public entry point. One Jev call per check, sync or async."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable, Mapping
from types import TracebackType
from typing import Any

from jev_guard import backends, telemetry
from jev_guard.client import JevBackend, JevResult
from jev_guard.cost import estimate_tokens
from jev_guard.errors import PolicyError
from jev_guard.guards.input_guard import input_state
from jev_guard.guards.output_guard import Context, output_state
from jev_guard.guards.streaming import AsyncCheck, SyncCheck, achecked_stream
from jev_guard.policies.base import Policy
from jev_guard.types import GuardStage, QuestionSpec, Verdict

DEFAULT_POLICY_ENV = "JEV_GUARD_DEFAULT_POLICY"

logger = logging.getLogger("jev_guard")

# Where Guards get their backend: the process-wide one from ``jev_guard.backends`` (Jev by
# default). Tests replace this with a fake. Not part of the public API.
_backend_factory: Callable[[], JevBackend] = backends.get_backend


def _resolve_policy(policy: str | Policy | None) -> Policy:
    if isinstance(policy, Policy):
        return policy
    name = policy or os.environ.get(DEFAULT_POLICY_ENV, "").strip() or "general"
    if not isinstance(name, str):
        raise PolicyError(f"policy must be a name or a Policy, got {type(policy).__name__}")
    if name.lower().endswith((".yaml", ".yml")):
        return Policy.from_yaml(name)
    return Policy.from_builtin(name)


class Guard:
    """Checks user inputs before your LLM call and LLM outputs after it.

    ``policy`` is a builtin name (``"general"``, ``"writing_app"``, ...), a path to a
    ``.yaml`` policy file, or a ``Policy``.
    With no policy, ``JEV_GUARD_DEFAULT_POLICY`` is used, then ``"general"``.

    Checks raise ``JevAPIError`` if Jev can't be reached; they never silently allow or block.
    """

    def __init__(self, policy: str | Policy | None = None) -> None:
        self.policy = _resolve_policy(policy)
        self._backend: JevBackend | None = None
        self._session: telemetry.Session | None = None
        telemetry.auto_setup()

    def _get_backend(self) -> JevBackend:
        if self._backend is None:
            self._backend = _backend_factory()
        return self._backend

    # --- sync -------------------------------------------------------------------------------

    def check_input(self, user_message: str) -> Verdict:
        """Check a user message before sending it to your LLM."""
        return self._run("input", input_state(user_message), _is_blank(user_message))

    def check_output(
        self, user_message: str, llm_response: str, context: Context = None
    ) -> Verdict:
        """Check an LLM response. Pass ``context`` (retrieved docs) for grounding checks."""
        state = output_state(user_message, llm_response, context)
        return self._run("output", state, _is_blank(llm_response), self._output_policy(context))

    # --- async ------------------------------------------------------------------------------

    async def acheck_input(self, user_message: str) -> Verdict:
        return await self._arun("input", input_state(user_message), _is_blank(user_message))

    async def acheck_output(
        self, user_message: str, llm_response: str, context: Context = None
    ) -> Verdict:
        state = output_state(user_message, llm_response, context)
        policy = self._output_policy(context)
        return await self._arun("output", state, _is_blank(llm_response), policy)

    # --- streaming --------------------------------------------------------------------------

    async def astream_check(
        self, llm_stream: AsyncIterable[Any] | Iterable[Any], user_message: str
    ) -> AsyncIterator[str]:
        """Yield the text of a streamed LLM response, checking it as it arrives.

        ``llm_stream`` may yield strings or raw OpenAI / Anthropic / LangChain chunks. The
        policy's ``stream_strategy`` picks buffer-and-check (default) or rollback. If a check
        blocks, the last item yielded is a ``StreamCut`` (a ``str`` with ``.verdict`` and
        ``.retract``) and the upstream stream is closed. See ``jev_guard.guards.streaming``.
        """
        _, check, policy = self._stream_checkers(user_message)
        async for token in achecked_stream(
            llm_stream,
            check,
            strategy=policy.stream_strategy,
            every=policy.stream_check_every,
            emit="text",
            on_cut="marker",
        ):
            yield token

    def _stream_checkers(self, user_message: str) -> tuple[SyncCheck, AsyncCheck, Policy]:
        """Output checks for a stream, resolving the policy (and any context warning) once."""
        policy = self._output_policy(None)

        def check(text: str) -> Verdict:
            return self._run("output", output_state(user_message, text), _is_blank(text), policy)

        async def acheck(text: str) -> Verdict:
            state = output_state(user_message, text)
            return await self._arun("output", state, _is_blank(text), policy)

        return check, acheck, policy

    # --- context manager --------------------------------------------------------------------

    def __enter__(self) -> Guard:
        """Group checks under one ``jev_guard.session`` span (when telemetry is on)."""
        self._session = telemetry.start_session(self.policy)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        telemetry.end_session(self._session, exc)
        self._session = None
        if self._backend is not None:
            self._backend.close()

    # --- internals --------------------------------------------------------------------------

    def _output_policy(self, context: Context) -> Policy:
        if context is not None or self.policy.context_fallback is None:
            return self.policy
        logger.warning(
            "jev-guard: policy %r needs context for output checks; none was passed, so "
            "%r output checks were used instead",
            self.policy.name,
            self.policy.context_fallback,
        )
        return self.policy.without_context()

    def _run(
        self, stage: GuardStage, state: dict[str, Any], blank: bool, policy: Policy | None = None
    ) -> Verdict:
        policy = policy or self.policy
        questions = policy.questions_for(stage)
        if blank or not questions:
            return self._skipped(stage, blank)
        wire = _wire(questions)
        with telemetry.check_span(policy, stage) as span:
            result = self._get_backend().evaluate(state, wire)
            verdict = self._verdict(policy, stage, result, state, wire)
            if span is not None:
                span.record(verdict, result.model)
        return verdict

    async def _arun(
        self, stage: GuardStage, state: dict[str, Any], blank: bool, policy: Policy | None = None
    ) -> Verdict:
        policy = policy or self.policy
        questions = policy.questions_for(stage)
        if blank or not questions:
            return self._skipped(stage, blank)
        wire = _wire(questions)
        with telemetry.check_span(policy, stage) as span:
            result = await self._get_backend().aevaluate(state, wire)
            verdict = self._verdict(policy, stage, result, state, wire)
            if span is not None:
                span.record(verdict, result.model)
        return verdict

    def _verdict(
        self,
        policy: Policy,
        stage: GuardStage,
        result: JevResult,
        state: dict[str, Any],
        wire: Mapping[str, Any],
    ) -> Verdict:
        tokens = result.input_tokens
        if tokens is None:  # the API didn't report usage; estimate so cost is never zero
            tokens = estimate_tokens({"state": state, "questions": wire})
        verdict = policy.build_verdict(
            stage,
            result.answers,
            latency_ms=result.latency_ms,
            input_tokens=tokens,
            model=result.model,
            cost_usd=result.cost_usd,
        )
        # Only the context fallback earns a note. Callers swap the policy for other reasons
        # too (ToolGuard drops questions that need a user_request), and those used to render
        # as "used None output checks".
        if policy is not self.policy and self.policy.context_fallback is not None:
            note = f"no context passed: used '{self.policy.context_fallback}' output checks"
            verdict = verdict.model_copy(update={"reasons": [*verdict.reasons, note]})
        return verdict

    def _skipped(self, stage: GuardStage, blank: bool) -> Verdict:
        why = "nothing to check (empty text)" if blank else f"policy has no {stage} questions"
        return Verdict(
            action="allow",
            stage=stage,
            confidence=1.0,
            raw_answers={},
            reasons=[f"skipped: {why}"],
            latency_ms=0.0,
            input_tokens_used=0,
            estimated_cost_usd=0.0,
            policy_name=self.policy.name,
            policy_version=self.policy.version,
        )


def _is_blank(text: str) -> bool:
    if not isinstance(text, str):
        raise TypeError(f"expected str, got {type(text).__name__}")
    return not text.strip()


def _wire(questions: Mapping[str, QuestionSpec]) -> dict[str, dict[str, object]]:
    return {name: spec.to_wire() for name, spec in questions.items()}
