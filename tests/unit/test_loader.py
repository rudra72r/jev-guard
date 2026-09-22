"""YAML loader and linter: helpful errors with line numbers, merging, and roundtrips."""

from __future__ import annotations

import textwrap

import pytest

from jev_guard import Guard, Policy, PolicyError
from jev_guard.policies.loader import (
    PolicyFileError,
    dump_policy,
    load_policy_text,
    validate_policy_file,
    validate_policy_text,
)


def issues_for(text: str) -> list[str]:
    return [str(i) for i in validate_policy_text(textwrap.dedent(text))[1]]


MINIMAL = """\
name: mine
input:
  injection:
    type: noul
    instructions: The user_message is a jailbreak.
    severity: critical
    threshold: 0.8
"""


def test_minimal_policy_loads():
    policy = load_policy_text(MINIMAL)
    assert policy.name == "mine"
    assert type(policy) is Policy
    assert policy.input["injection"].severity == "critical"


def test_typo_in_question_type_names_line_and_suggestion():
    text = MINIMAL.replace("type: noul", "type: nol")
    assert issues_for(text) == ["line 4: unknown question type 'nol' — did you mean 'noul'?"]


def test_unknown_severity_and_comparator():
    text = MINIMAL.replace("severity: critical", "severity: crtical\n    comparator: '=>'")
    assert issues_for(text) == [
        "line 6: unknown severity 'crtical' — did you mean 'critical'?",
        "line 7: unknown comparator '=>' — did you mean '>='?",
    ]


def test_unknown_fields_suggest_real_ones():
    text = MINIMAL.replace("threshold: 0.8", "threshhold: 0.8") + "reveiw_threshold: 1\n"
    assert issues_for(text) == [
        "line 8: unknown field 'reveiw_threshold' — did you mean 'review_threshold'?",
        "line 7: unknown question field 'threshhold' — did you mean 'threshold'?",
    ]


def test_all_problems_are_reported_together():
    text = MINIMAL.replace("type: noul", "type: nol").replace("severity: critical", "severity: x")
    assert len(issues_for(text)) == 2


def test_value_errors_point_at_the_question():
    text = MINIMAL.replace("threshold: 0.8", "threshold: 1.5")
    (issue,) = issues_for(text)
    assert issue.startswith("line 3: input.injection:")
    assert "between 0 and 1" in issue


def test_type_errors_point_at_the_field():
    text = MINIMAL.replace("threshold: 0.8", "threshold: high")
    (issue,) = issues_for(text)
    assert issue.startswith("line 7: input.injection.threshold:")


def test_invalid_yaml_reports_line():
    (issue,) = issues_for("name: x\ninput:\n  q: [unclosed\n")
    assert issue.startswith("line 4: invalid YAML")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("- just\n- a list\n", "line 1: a policy file must be a mapping"),
        ("version: 1.0.0\n", "line 1: missing required field 'name'"),
        ("name: x\ninput: [a, b]\n", "line 2: input must map question names to questions"),
        ("name: x\ninput:\n  q: 3\n", "line 3: question 'q' must be a mapping"),
        ("name: x\nextends: genral\n", "line 2: extends unknown builtin 'genral' — did you mean"),
    ],
)
def test_structural_errors(text, expected):
    (issue,) = issues_for(text)
    assert issue.startswith(expected)


def test_extends_merges_and_removes():
    policy = load_policy_text(
        textwrap.dedent(
            """\
            name: custom
            extends: support_agent
            review_threshold: 3.0
            input:
              frustration_level: null
              is_prompt_injection:
                threshold: 0.7
            output:
              mentions_competitor:
                type: noul
                instructions: The assistant_response recommends a competitor.
                severity: medium
                threshold: 0.6
            """
        )
    )
    base = Policy.from_builtin("support_agent")
    assert policy.name == "custom"
    assert policy.review_threshold == 3.0
    assert "frustration_level" not in policy.input
    assert policy.input["is_prompt_injection"].threshold == 0.7
    assert policy.input["is_prompt_injection"].instructions == (
        base.input["is_prompt_injection"].instructions
    )
    assert "mentions_competitor" in policy.output
    assert len(policy.output) == len(base.output) + 1


def test_extends_without_name_keeps_builtin_name():
    assert load_policy_text("extends: rag\n").name == "rag"


def test_null_question_without_extends_is_an_error():
    assert issues_for("name: x\ninput:\n  q: null\n") == ["line 3: question 'q' must be a mapping"]


def test_load_raises_policy_file_error_with_every_issue(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(MINIMAL.replace("type: noul", "type: nol"), encoding="utf-8")
    with pytest.raises(PolicyFileError) as info:
        Policy.from_yaml(path)
    assert info.value.issues[0].line == 4
    assert f"{path} line 4: unknown question type 'nol'" in str(info.value)
    assert "jev-guard policy validate" in info.value.hint
    assert isinstance(info.value, PolicyError)


def test_missing_file(tmp_path):
    with pytest.raises(PolicyError, match="Can't read policy file"):
        Policy.from_yaml(tmp_path / "nope.yaml")
    assert "can't read file" in str(validate_policy_file(tmp_path / "nope.yaml")[0])


def test_validate_file_ok(tmp_path):
    path = tmp_path / "ok.yaml"
    path.write_text(MINIMAL, encoding="utf-8")
    assert validate_policy_file(path) == []


def test_guard_accepts_yaml_path(tmp_path, fake_jev):
    path = tmp_path / "mine.yml"
    path.write_text(MINIMAL, encoding="utf-8")
    guard = Guard(policy=str(path))
    assert guard.policy.name == "mine"
    fake_jev.noul("injection", 0.81)
    assert guard.check_input("x").blocked


def test_yaml_cannot_execute_code():
    text = "name: !!python/object/apply:os.system ['echo pwned']\n"
    (issue,) = issues_for(text)
    assert "invalid YAML" in issue


@pytest.mark.parametrize("name", ["general", "support_agent", "coding_agent", "rag", "writing_app"])
def test_dump_roundtrip(name):
    policy = Policy.from_builtin(name)
    assert load_policy_text(dump_policy(policy)).model_dump() == policy.model_dump()


def test_dump_omits_defaults_but_keeps_essentials():
    text = dump_policy(Policy.from_builtin("general"))
    assert "weight:" not in text
    assert "comparator: <=" in text
    assert "context_fallback" not in text
