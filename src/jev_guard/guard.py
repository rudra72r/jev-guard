"""``Guard``: the public entry point. One Jev call per check, sync or async."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any

from jev_guard.client import JevBackend, JevClient, JevResult
from jev_guard.cost import estimate_tokens
from jev_guard.errors import PolicyError
from jev_guard.guards.input_guard import input_state
from jev_guard.guards.output_guard import Context, output_state
from jev_guard.policies.base import Policy
from jev_guard.types import GuardStage, QuestionSpec, Verdict

DEFAULT_POLICY_ENV = "JEV_GUARD_DEFAULT_POLICY"

# Tests replace this to run Guard against a fake Jev. Not part of the public API.
_backend_factory: Callable[[], JevBackend] = JevClient


def _resolve_policy(policy: str | Policy | None) -> Policy:
    if isinstance(policy, Policy):
        return policy
    name = policy or os.environ.get(DEFAULT_POLICY_ENV, "").strip() or "general"
    if not isinstance(name, str):
        raise PolicyError(f"policy must be a name or a Policy, got {type(policy).__name__}")
    return Policy.from_builtin(name)


class Guard:
    """Checks user inputs before your LLM call and LLM outputs after it.

    ``policy`` is a builtin name (``"general"``, ``"writing_app"``, ...) or a ``Policy``.
    With no policy, ``JEV_GUARD_DEFAULT_POLICY`` is used, then ``"general"``.

    Checks raise ``JevAPIError`` if Jev can't be reached; they never silently allow or block.
    """

    def __init__(self, policy: str | Policy | None = None) -> None:
        self.policy = _resolve_policy(policy)
        self._backend: JevBackend | None = None

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
        return self._run("output", state, _is_blank(llm_response))

    # --- async ------------------------------------------------------------------------------

    async def acheck_input(self, user_message: str) -> Verdict:
        return await self._arun("input", input_state(user_message), _is_blank(user_message))

    async def acheck_output(
        self, user_message: str, llm_response: str, context: Context = None
    ) -> Verdict:
        state = output_state(user_message, llm_response, context)
        return await self._arun("output", state, _is_blank(llm_response))

    # --- context manager --------------------------------------------------------------------

    def __enter__(self) -> Guard:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._backend is not None:
            self._backend.close()

    # --- internals --------------------------------------------------------------------------

    def _run(self, stage: GuardStage, state: dict[str, Any], blank: bool) -> Verdict:
        questions = self.policy.questions_for(stage)
        if blank or not questions:
            return self._skipped(stage, blank)
        wire = _wire(questions)
        result = self._get_backend().evaluate(state, wire)
        return self._verdict(stage, result, state, wire)

    async def _arun(self, stage: GuardStage, state: dict[str, Any], blank: bool) -> Verdict:
        questions = self.policy.questions_for(stage)
        if blank or not questions:
            return self._skipped(stage, blank)
        wire = _wire(questions)
        result = await self._get_backend().aevaluate(state, wire)
        return self._verdict(stage, result, state, wire)

    def _verdict(
        self,
        stage: GuardStage,
        result: JevResult,
        state: dict[str, Any],
        wire: Mapping[str, Any],
    ) -> Verdict:
        tokens = result.input_tokens
        if tokens is None:  # the API didn't report usage; estimate so cost is never zero
            tokens = estimate_tokens({"state": state, "questions": wire})
        return self.policy.build_verdict(
            stage,
            result.answers,
            latency_ms=result.latency_ms,
            input_tokens=tokens,
            model=result.model,
        )

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
