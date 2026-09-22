"""Core data types: question specs, Jev answers, and the Verdict every check returns."""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

Action: TypeAlias = Literal["allow", "review", "block"]
GuardStage: TypeAlias = Literal["input", "output"]
Severity: TypeAlias = Literal["low", "medium", "high", "critical"]
QuestionKind: TypeAlias = Literal["noul", "choice", "score"]
Comparator: TypeAlias = Literal[">", ">=", "<", "<="]

# Used when a question doesn't set its own weight. Critical questions dominate the
# aggregate confidence; low ones barely move it.
DEFAULT_WEIGHTS: dict[Severity, float] = {"low": 0.5, "medium": 1.0, "high": 2.0, "critical": 3.0}

_MIN_SCORE_LEVELS = 2
_MAX_SCORE_LEVELS = 10
_MAX_CHOICES = 255


class QuestionSpec(BaseModel):
    """One question a policy asks Jev, plus the rule that decides when it "fires".

    - ``noul``: fires when the probability compares true against ``threshold`` (0-1).
    - ``choice``: fires when Jev picks a label in ``flag`` and its confidence compares true
      against ``threshold``.
    - ``score``: fires when the score (in rubric levels, 0 to len(criteria)-1) compares true
      against ``threshold``.

    ``risk_when`` says which end is risky for nouls and scores: ``"low"`` for questions like
    "the answer is grounded in the context", where a low value is the bad outcome.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: QuestionKind
    instructions: str = Field(min_length=1)
    criteria: dict[str, str] | list[str] | None = None
    severity: Severity = "medium"
    weight: float | None = Field(default=None, ge=0)
    threshold: float
    comparator: Comparator = ">"
    flag: list[str] = Field(default_factory=list)
    risk_when: Literal["high", "low"] = "high"

    @property
    def effective_weight(self) -> float:
        return self.weight if self.weight is not None else DEFAULT_WEIGHTS[self.severity]

    @property
    def max_level(self) -> int:
        """Highest rubric level for a score question (levels start at 0)."""
        return len(self.criteria) - 1 if isinstance(self.criteria, list) else 0

    @model_validator(mode="after")
    def _check_shape(self) -> QuestionSpec:
        if self.type == "noul":
            self._check_noul()
        elif self.type == "choice":
            self._check_choice()
        else:
            self._check_score()
        return self

    def _check_noul(self) -> None:
        if self.criteria is not None and (
            not isinstance(self.criteria, dict) or not set(self.criteria) <= {"true", "false"}
        ):
            raise ValueError("noul criteria may only describe 'true' and/or 'false'")
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(f"noul threshold must be between 0 and 1, got {self.threshold}")

    def _check_choice(self) -> None:
        if not isinstance(self.criteria, dict) or not self.criteria:
            raise ValueError("choice questions need criteria as a mapping of label -> description")
        if len(self.criteria) > _MAX_CHOICES:
            raise ValueError(f"choice questions allow at most {_MAX_CHOICES} labels")
        if not self.flag:
            raise ValueError("choice questions need `flag`: the labels that count as risky")
        unknown = [label for label in self.flag if label not in self.criteria]
        if unknown:
            raise ValueError(f"flag labels {unknown} are not in criteria {list(self.criteria)}")
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(f"choice threshold must be between 0 and 1, got {self.threshold}")

    def _check_score(self) -> None:
        if not isinstance(self.criteria, list):
            raise ValueError("score questions need criteria as an ordered list, lowest level first")
        if not _MIN_SCORE_LEVELS <= len(self.criteria) <= _MAX_SCORE_LEVELS:
            raise ValueError(
                f"score questions need {_MIN_SCORE_LEVELS}-{_MAX_SCORE_LEVELS} levels, "
                f"got {len(self.criteria)}"
            )
        if not 0 <= self.threshold <= self.max_level:
            raise ValueError(
                f"score threshold must be between 0 and {self.max_level}, got {self.threshold}"
            )

    def to_wire(self) -> dict[str, object]:
        """The question in the dict form the TypeSafe SDK accepts."""
        wire: dict[str, object] = {"type": self.type, "instructions": self.instructions}
        if self.criteria is not None:
            wire["criteria"] = self.criteria
        return wire


class JevAnswer(BaseModel):
    """Jev's answer to one question, decoupled from SDK types so it stays JSON-friendly.

    Only the fields for ``type`` are set: nouls have just ``noul``; choices have ``choice``,
    ``confidence`` and ``probabilities``; scores have ``score``, ``confidence`` and
    ``probabilities`` (keyed by level as a string).
    """

    model_config = ConfigDict(frozen=True)

    type: QuestionKind
    noul: float | None = None
    choice: str | None = None
    score: float | None = None
    confidence: float | None = None
    probabilities: dict[str, float] | None = None


class Verdict(BaseModel):
    """The result of one guard check. Serialize with ``.model_dump()``."""

    model_config = ConfigDict(frozen=True)

    action: Action
    stage: GuardStage
    confidence: float = Field(ge=0.0, le=1.0)
    raw_answers: dict[str, JevAnswer]
    reasons: list[str]
    latency_ms: float
    input_tokens_used: int
    estimated_cost_usd: float
    suggested_response: str | None = None
    policy_name: str
    policy_version: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blocked(self) -> bool:
        return self.action == "block"
