"""Every builtin's YAML twin in policies/ is equivalent to the Python definition.

Behavioural equivalence is checked on 300 seeded random answer sets per stage. Once the
golden dataset lands (Phase 5), the eval suite checks the same thing on real samples.
"""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest

from jev_guard import Policy
from jev_guard.policies import BUILTIN_POLICIES
from jev_guard.types import JevAnswer, QuestionSpec

ROOT = Path(__file__).resolve().parents[2]
POLICY_DIR = ROOT / "policies"
SAMPLES = ("strict", "permissive", "coding_agent_prod", "general_local")


def _load_exporter():
    spec = importlib.util.spec_from_file_location("export", ROOT / "scripts/export_policies.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def random_answer(spec: QuestionSpec, rng: random.Random) -> JevAnswer:
    if spec.type == "noul":
        return JevAnswer(type="noul", noul=rng.random())
    if spec.type == "choice":
        assert isinstance(spec.criteria, dict)
        labels = list(spec.criteria)
        weights = [rng.random() for _ in labels]
        total = sum(weights)
        probabilities = {label: w / total for label, w in zip(labels, weights, strict=True)}
        best = max(probabilities, key=probabilities.__getitem__)
        return JevAnswer(
            type="choice",
            choice=best,
            confidence=probabilities[best],
            probabilities=probabilities,
        )
    return JevAnswer(type="score", score=rng.uniform(0, spec.max_level), confidence=rng.random())


@pytest.mark.parametrize("name", BUILTIN_POLICIES)
def test_twin_file_exists_and_matches_definition(name):
    builtin = Policy.from_builtin(name)
    from_yaml = Policy.from_yaml(POLICY_DIR / f"{name}.yaml")
    assert from_yaml.model_dump() == builtin.model_dump()


@pytest.mark.parametrize("name", BUILTIN_POLICIES)
def test_twin_file_is_up_to_date(name):
    expected = _load_exporter().render(name)
    actual = (POLICY_DIR / f"{name}.yaml").read_text(encoding="utf-8")
    assert actual == expected, "run `python scripts/export_policies.py`"


@pytest.mark.parametrize("name", BUILTIN_POLICIES)
@pytest.mark.parametrize("stage", ["input", "output"])
def test_twin_behaves_identically(name, stage):
    builtin = Policy.from_builtin(name)
    from_yaml = Policy.from_yaml(POLICY_DIR / f"{name}.yaml")
    rng = random.Random(f"{name}-{stage}")
    actions = set()
    for _ in range(300):
        answers = {q: random_answer(s, rng) for q, s in builtin.questions_for(stage).items()}
        expected = builtin.aggregate(stage, answers)
        assert from_yaml.aggregate(stage, answers) == expected
        actions.add(expected.action)
    assert "allow" in actions or not builtin.questions_for(stage)


@pytest.mark.parametrize("name", SAMPLES)
def test_sample_policies_load(name):
    policy = Policy.from_yaml(POLICY_DIR / f"{name}.yaml")
    assert policy.name == name


def test_strict_is_stricter_than_general():
    general = Policy.from_builtin("general")
    strict = Policy.from_yaml(POLICY_DIR / "strict.yaml")
    assert (
        strict.input["is_prompt_injection"].threshold
        < general.input["is_prompt_injection"].threshold
    )
    assert strict.block_threshold == 3.0
    assert strict.output["is_off_topic"].severity == "high"
    # untouched fields are inherited
    assert strict.input["intent"].criteria == general.input["intent"].criteria


def test_permissive_removes_questions():
    permissive = Policy.from_yaml(POLICY_DIR / "permissive.yaml")
    assert "contains_pii" not in permissive.input
    assert set(permissive.output) == {"is_off_topic"}


def test_coding_agent_prod_keeps_builtin_class():
    prod = Policy.from_yaml(POLICY_DIR / "coding_agent_prod.yaml")
    assert type(prod) is type(Policy.from_builtin("coding_agent"))
    assert prod.input["attempts_privilege_escalation"].severity == "critical"
