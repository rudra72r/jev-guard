"""The Policy base class, the builtin registry, and the Section 6 aggregation rules.

How a stage's answers become an action:

1. Each question *fires* when its value crosses its threshold (see ``QuestionSpec``).
2. Any fired ``critical`` question blocks on its own.
3. Fired ``high`` questions add their weight to a sum. Sum > ``block_threshold`` blocks,
   sum > ``review_threshold`` reviews. So one high signal asks for review and several
   together block, which keeps false positives down when a policy has 5+ questions.
4. ``medium`` and ``low`` questions never drive the action. They are still listed in
   ``reasons`` when they fire, and they pull down ``confidence``.
5. ``confidence = 1 - sum(weight * risk) / sum(weight)`` over every answered question, where
   risk is 0-1 (a noul's probability, a flagged choice's probability, a score's position on
   the risky end of its rubric). Totally clean -> 1.0, totally suspicious -> 0.0.

Comparisons are strict by default: a value exactly equal to its threshold does not fire.
"""

from __future__ import annotations

import difflib
import importlib
import logging
import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jev_guard.cost import estimate_cost_usd
from jev_guard.errors import PolicyError
from jev_guard.types import (
    Action,
    Comparator,
    GuardStage,
    JevAnswer,
    QuestionSpec,
    Verdict,
)

logger = logging.getLogger("jev_guard")

_COMPARE: dict[Comparator, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}

_REGISTRY: dict[str, type[Policy]] = {}


@dataclass(frozen=True, slots=True)
class Outcome:
    """How one question scored: the compared value, its 0-1 risk, and whether it fired."""

    name: str
    spec: QuestionSpec
    value: float
    risk: float
    fired: bool
    label: str | None = None  # the chosen label, for choice questions

    def reason(self) -> str:
        spec = self.spec
        shown = (
            f"{self.label} @ {self.value:.2f}" if self.label is not None else f"{self.value:.2f}"
        )
        return f"{self.name}: {shown} {spec.comparator} {spec.threshold:.2f} ({spec.severity})"


@dataclass(frozen=True, slots=True)
class Aggregation:
    action: Action
    confidence: float
    reasons: list[str]
    fired: list[str]


def score_question(name: str, spec: QuestionSpec, answer: JevAnswer) -> Outcome | None:
    """Turn one Jev answer into an Outcome, or None if the answer doesn't match the question."""
    compare = _COMPARE[spec.comparator]
    if spec.kind == "noul" and answer.noul is not None:
        return Outcome(name, spec, answer.noul, answer.noul, compare(answer.noul, spec.threshold))
    if spec.kind == "choice" and answer.choice is not None:
        confidence = answer.confidence if answer.confidence is not None else 0.0
        probabilities = answer.probabilities or {}
        risk = sum(probabilities.get(label, 0.0) for label in spec.flag)
        if not probabilities and answer.choice in spec.flag:
            risk = confidence
        fired = answer.choice in spec.flag and compare(confidence, spec.threshold)
        return Outcome(name, spec, confidence, min(risk, 1.0), fired, label=answer.choice)
    if spec.kind == "score" and answer.score is not None:
        position = answer.score / spec.max_level
        risk = position if spec.risk_when == "high" else 1.0 - position
        return Outcome(
            name,
            spec,
            answer.score,
            min(max(risk, 0.0), 1.0),
            compare(answer.score, spec.threshold),
        )
    return None


class Policy(BaseModel):
    """A named set of Jev questions per stage plus the thresholds that turn answers into actions.

    Subclass it and set field defaults to define a builtin; every subclass with a ``name``
    default registers itself for ``Policy.from_builtin``. Override ``aggregate`` to change how
    answers become an action.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str = "1.0.0"
    description: str = ""
    input: dict[str, QuestionSpec] = Field(default_factory=dict)
    output: dict[str, QuestionSpec] = Field(default_factory=dict)
    review_threshold: float = Field(default=1.0, ge=0.0)
    block_threshold: float | None = Field(default=None, ge=0.0)
    suggested_responses: dict[str, str] = Field(default_factory=dict)
    default_suggested_response: str | None = "Sorry, I can't help with that."

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        default = cls.model_fields["name"].default
        if isinstance(default, str) and default:
            _REGISTRY[default] = cls

    # --- construction -------------------------------------------------------------------

    @classmethod
    def from_builtin(cls, name: str) -> Policy:
        """Load a builtin policy by name, e.g. ``Policy.from_builtin("support_agent")``."""
        importlib.import_module("jev_guard.policies")  # registers the builtins
        policy_cls = _REGISTRY.get(name)
        if policy_cls is None:
            raise PolicyError(f"Unknown policy {name!r}.{_did_you_mean(name, _REGISTRY)}")
        return policy_cls.model_validate({})  # registered subclasses default every field

    @classmethod
    def available(cls) -> list[str]:
        importlib.import_module("jev_guard.policies")
        return sorted(_REGISTRY)

    def override(self, *, thresholds: Mapping[str, float] | None = None) -> Policy:
        """Return a copy with some question thresholds changed. Applies to both stages."""
        thresholds = dict(thresholds or {})
        known = set(self.input) | set(self.output)
        for name in thresholds:
            if name not in known:
                raise PolicyError(
                    f"Policy {self.name!r} has no question {name!r}.{_did_you_mean(name, known)}",
                    hint=f"Run `jev-guard policy show {self.name}` to list its questions.",
                )

        def patch(questions: dict[str, QuestionSpec]) -> dict[str, Any]:
            return {
                q: spec.model_dump() | ({"threshold": thresholds[q]} if q in thresholds else {})
                for q, spec in questions.items()
            }

        data = self.model_dump() | {"input": patch(self.input), "output": patch(self.output)}
        try:
            return type(self).model_validate(data)
        except ValidationError as err:
            raise PolicyError(f"Invalid threshold override: {err}") from err

    # --- evaluation ---------------------------------------------------------------------

    def questions_for(self, stage: GuardStage) -> dict[str, QuestionSpec]:
        return self.input if stage == "input" else self.output

    def aggregate(self, stage: GuardStage, answers: Mapping[str, JevAnswer]) -> Aggregation:
        outcomes: list[Outcome] = []
        reasons: list[str] = []
        for name, spec in self.questions_for(stage).items():
            answer = answers.get(name)
            outcome = score_question(name, spec, answer) if answer is not None else None
            if outcome is None:
                logger.warning("jev-guard: no usable answer for %r; treated as not fired", name)
                reasons.append(
                    f"{name}: no answer from Jev, treated as not fired ({spec.severity})"
                )
                continue
            outcomes.append(outcome)

        fired = [o for o in outcomes if o.fired]
        reasons = [o.reason() for o in fired] + reasons
        high_sum = sum(o.spec.effective_weight for o in fired if o.spec.severity == "high")

        action: Action = "allow"
        if any(o.spec.severity == "critical" for o in fired):
            action = "block"
        elif self.block_threshold is not None and high_sum > self.block_threshold:
            action = "block"
            reasons.append(f"high-severity sum {high_sum:.2f} > block {self.block_threshold:.2f}")
        elif high_sum > self.review_threshold:
            action = "review"
            reasons.append(f"high-severity sum {high_sum:.2f} > review {self.review_threshold:.2f}")

        total_weight = sum(o.spec.effective_weight for o in outcomes)
        weighted_risk = sum(o.spec.effective_weight * o.risk for o in outcomes)
        confidence = 1.0 - weighted_risk / total_weight if total_weight else 1.0
        return Aggregation(
            action=action,
            confidence=min(max(confidence, 0.0), 1.0),
            reasons=reasons,
            fired=[o.name for o in fired],
        )

    def suggested_response_for(self, fired: list[str]) -> str | None:
        for name in fired:
            if name in self.suggested_responses:
                return self.suggested_responses[name]
        return self.default_suggested_response

    def build_verdict(
        self,
        stage: GuardStage,
        answers: Mapping[str, JevAnswer],
        *,
        latency_ms: float,
        input_tokens: int,
        model: str | None,
    ) -> Verdict:
        result = self.aggregate(stage, answers)
        return Verdict(
            action=result.action,
            stage=stage,
            confidence=result.confidence,
            raw_answers=dict(answers),
            reasons=result.reasons,
            latency_ms=latency_ms,
            input_tokens_used=input_tokens,
            estimated_cost_usd=estimate_cost_usd(input_tokens, model),
            suggested_response=(
                self.suggested_response_for(result.fired) if result.action == "block" else None
            ),
            policy_name=self.name,
            policy_version=self.version,
        )


def _did_you_mean(name: str, options: Any) -> str:
    matches = difflib.get_close_matches(name, list(options), n=1)
    if matches:
        return f" Did you mean {matches[0]!r}?"
    return f" Available: {', '.join(sorted(options))}." if options else ""
