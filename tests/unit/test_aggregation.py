"""Section 6 aggregation math: weights, severities, criticals, strict comparisons, confidence."""

from __future__ import annotations

import pytest
from pydantic import Field, ValidationError

from jev_guard.policies.base import Policy, score_question
from jev_guard.types import JevAnswer, QuestionSpec


def noul(severity: str, threshold: float = 0.5, weight: float | None = None) -> QuestionSpec:
    return QuestionSpec(
        kind="noul", instructions="x", severity=severity, threshold=threshold, weight=weight
    )


def answers(**values: float) -> dict[str, JevAnswer]:
    return {name: JevAnswer(type="noul", noul=v) for name, v in values.items()}


def make_policy(block_threshold: float | None = 3.0, review_threshold: float = 1.0) -> Policy:
    return Policy(
        name="custom",
        input={
            "a": noul("high"),
            "b": noul("high"),
            "c": noul("high"),
            "m": noul("medium"),
            "l": noul("low"),
        },
        review_threshold=review_threshold,
        block_threshold=block_threshold,
    )


def test_all_clean_is_allow_with_confidence_one():
    agg = make_policy().aggregate("input", answers(a=0, b=0, c=0, m=0, l=0))
    assert agg.action == "allow"
    assert agg.confidence == 1.0


def test_everything_maxed_gives_confidence_zero():
    agg = make_policy().aggregate("input", answers(a=1, b=1, c=1, m=1, l=1))
    assert agg.confidence == 0.0


def test_one_high_reviews_two_highs_review_three_block():
    policy = make_policy(block_threshold=4.5)  # each high weighs 2.0
    assert policy.aggregate("input", answers(a=0.9, b=0, c=0, m=0, l=0)).action == "review"
    assert policy.aggregate("input", answers(a=0.9, b=0.9, c=0, m=0, l=0)).action == "review"
    three = policy.aggregate("input", answers(a=0.9, b=0.9, c=0.9, m=0, l=0))
    assert three.action == "block"
    assert "high-severity sum 6.00 > block 4.50" in three.reasons


def test_sum_equal_to_block_threshold_does_not_block():
    policy = make_policy(block_threshold=4.0)
    assert policy.aggregate("input", answers(a=0.9, b=0.9, c=0, m=0, l=0)).action == "review"


def test_medium_and_low_never_drive_action():
    agg = make_policy().aggregate("input", answers(a=0, b=0, c=0, m=1, l=1))
    assert agg.action == "allow"
    assert set(agg.fired) == {"m", "l"}
    assert agg.confidence < 1.0


def test_critical_blocks_alone():
    policy = Policy(name="c", input={"crit": noul("critical", 0.75), "h": noul("high")})
    agg = policy.aggregate("input", answers(crit=0.8, h=0))
    assert agg.action == "block"
    assert agg.reasons == ["crit: 0.80 > 0.75 (critical)"]


def test_confidence_is_weighted_by_severity():
    policy = Policy(name="w", input={"crit": noul("critical"), "low": noul("low")})
    # weights 3.0 and 0.5: 1 - (3*1 + 0.5*0) / 3.5
    agg = policy.aggregate("input", answers(crit=1.0, low=0.0))
    assert agg.confidence == pytest.approx(1 - 3 / 3.5)


def test_explicit_weight_overrides_severity_default():
    policy = Policy(name="w", input={"a": noul("high", weight=0.4)}, review_threshold=0.5)
    assert policy.aggregate("input", answers(a=0.9)).action == "allow"


def test_missing_answer_is_reported_and_not_fired():
    policy = Policy(name="m", input={"crit": noul("critical"), "h": noul("high")})
    agg = policy.aggregate("input", answers(h=0.0))
    assert agg.action == "allow"
    assert agg.reasons == ["crit: no answer from Jev, treated as not fired (critical)"]


def test_mismatched_answer_type_is_treated_as_missing():
    spec = noul("high")
    assert score_question("q", spec, JevAnswer(type="choice", choice="x", confidence=1.0)) is None


def test_choice_risk_uses_flag_probability_even_when_not_chosen():
    spec = QuestionSpec(
        kind="choice",
        instructions="x",
        criteria={"ok": "fine", "bad": "not fine"},
        flag=["bad"],
        threshold=0.5,
    )
    answer = JevAnswer(
        type="choice", choice="ok", confidence=0.6, probabilities={"ok": 0.6, "bad": 0.4}
    )
    outcome = score_question("q", spec, answer)
    assert outcome is not None
    assert not outcome.fired
    assert outcome.risk == pytest.approx(0.4)


def test_choice_without_probabilities_falls_back_to_confidence():
    spec = QuestionSpec(
        kind="choice", instructions="x", criteria={"ok": "", "bad": ""}, flag=["bad"], threshold=0.5
    )
    outcome = score_question("q", spec, JevAnswer(type="choice", choice="bad", confidence=0.7))
    assert outcome is not None
    assert outcome.fired
    assert outcome.risk == 0.7


def test_score_risk_direction():
    levels = ["a", "b", "c", "d"]
    high_bad = QuestionSpec(
        kind="score", instructions="x", criteria=levels, threshold=2, comparator=">="
    )
    low_bad = QuestionSpec(
        kind="score",
        instructions="x",
        criteria=levels,
        threshold=1,
        comparator="<=",
        risk_when="low",
    )
    answer = JevAnswer(type="score", score=3.0)
    high = score_question("q", high_bad, answer)
    low = score_question("q", low_bad, answer)
    assert high is not None
    assert low is not None
    assert (high.fired, high.risk) == (True, 1.0)
    assert (low.fired, low.risk) == (False, 0.0)


def test_policy_without_questions_for_stage_has_confidence_one():
    agg = Policy(name="empty").aggregate("output", {})
    assert (agg.action, agg.confidence) == ("allow", 1.0)


def test_subclass_with_name_registers_as_builtin():
    class TeamPolicy(Policy):
        name: str = "team_test_policy"
        input: dict[str, QuestionSpec] = Field(default_factory=lambda: {"a": noul("critical")})

    assert Policy.from_builtin("team_test_policy").input["a"].severity == "critical"
    assert "team_test_policy" in Policy.available()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"kind": "noul", "threshold": 1.2}, "between 0 and 1"),
        ({"kind": "noul", "threshold": 0.5, "criteria": {"maybe": "x"}}, "'true' and/or 'false'"),
        ({"kind": "choice", "threshold": 0.5}, "mapping of label"),
        ({"kind": "choice", "threshold": 0.5, "criteria": {"a": "x"}}, "need `flag`"),
        ({"kind": "choice", "threshold": 0.5, "criteria": {"a": "x"}, "flag": ["z"]}, "not in"),
        ({"kind": "choice", "threshold": 2, "criteria": {"a": "x"}, "flag": ["a"]}, "between 0"),
        ({"kind": "score", "threshold": 1}, "ordered list"),
        ({"kind": "score", "threshold": 0, "criteria": ["only one"]}, "2-10 levels"),
        ({"kind": "score", "threshold": 5, "criteria": ["a", "b", "c"]}, "between 0 and 2"),
    ],
)
def test_question_spec_validation(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        QuestionSpec(instructions="x", **kwargs)


def test_too_many_choices_rejected():
    criteria = {str(i): "" for i in range(256)}
    with pytest.raises(ValidationError, match="at most 255"):
        QuestionSpec(kind="choice", instructions="x", criteria=criteria, flag=["0"], threshold=0.5)


def test_to_wire_omits_missing_criteria():
    assert noul("low").to_wire() == {"type": "noul", "instructions": "x"}
