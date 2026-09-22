"""Precision / recall / F1 for labelled scans and the golden eval.

Labels come in two shapes:
- an action (``"allow"`` / ``"review"`` / ``"block"``, or a bool): positive means "should
  have been flagged", i.e. anything other than ``allow``;
- a per-question mapping (``{"is_prompt_injection": true}``): positive means that question
  should have fired, in either stage.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from jev_guard.policies.base import Policy, score_question
from jev_guard.types import Verdict

_ACTION_RANK = {"allow": 0, "review": 1, "block": 2}


@dataclass(slots=True)
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, predicted: bool, actual: bool) -> None:
        if predicted and actual:
            self.tp += 1
        elif predicted:
            self.fp += 1
        elif actual:
            self.fn += 1
        else:
            self.tn += 1

    @property
    def support(self) -> int:
        return self.tp + self.fn

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "support": self.support,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


@dataclass(slots=True)
class Metrics:
    flagged: Counts = field(default_factory=Counts)
    per_question: dict[str, Counts] = field(default_factory=dict)
    labelled: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "labelled": self.labelled,
            "flagged": self.flagged.as_dict(),
            "per_question": {q: c.as_dict() for q, c in sorted(self.per_question.items())},
        }


def worst_action(verdicts: Iterable[Verdict | None]) -> str:
    actions = [v.action for v in verdicts if v is not None]
    return max(actions, key=_ACTION_RANK.__getitem__) if actions else "allow"


def fired_questions(policy: Policy, verdicts: Iterable[Verdict | None]) -> set[str]:
    """Names of every question that fired in these verdicts, recomputed from raw answers."""
    fired: set[str] = set()
    for verdict in verdicts:
        if verdict is None:
            continue
        specs = policy.questions_for(verdict.stage)
        for name, answer in verdict.raw_answers.items():
            spec = specs.get(name)
            outcome = score_question(name, spec, answer) if spec is not None else None
            if outcome is not None and outcome.fired:
                fired.add(name)
    return fired


def add_example(
    metrics: Metrics,
    policy: Policy,
    label: str | dict[str, bool],
    verdicts: list[Verdict | None],
) -> None:
    metrics.labelled += 1
    if isinstance(label, str):
        metrics.flagged.add(worst_action(verdicts) != "allow", label != "allow")
        return
    fired = fired_questions(policy, verdicts)
    for question, expected in label.items():
        metrics.per_question.setdefault(question, Counts()).add(question in fired, expected)
    metrics.flagged.add(worst_action(verdicts) != "allow", any(label.values()))
