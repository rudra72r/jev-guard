"""The golden eval runner, its report, and `jev-guard eval`, against fake Jev."""

from __future__ import annotations

import hashlib
import json

import pytest
from typer.testing import CliRunner

from jev_guard import Policy
from jev_guard._cli_app import app
from jev_guard.errors import ConfigurationError
from jev_guard.eval.golden import (
    estimate_eval_cost,
    load_golden,
    run_golden,
)
from jev_guard.eval.report import build_eval_report, render_html, render_markdown
from jev_guard.types import JevAnswer

GENERAL = Policy.from_builtin("general")
GOLDEN = load_golden()
runner = CliRunner()


@pytest.fixture
def oracle(fake_jev):
    """Answers every golden sample perfectly, from its label."""
    labels = {s.input: s.label for s in GOLDEN.samples}
    original = fake_jev.evaluate

    def evaluate(state, questions):
        label = labels.get(state["user_message"], {})
        fake_jev.answers.clear()
        fake_jev.noul("is_prompt_injection", 0.97 if label.get("is_prompt_injection") else 0.02)
        fake_jev.noul("contains_pii", 0.95 if label.get("contains_pii") else 0.02)
        if label.get("intent"):
            fake_jev.choice("intent", "malicious", 0.93, benign=0.02, borderline=0.05)
        return original(state, questions)

    fake_jev.evaluate = evaluate
    return fake_jev


async def test_perfect_detector_passes(oracle):
    result = await run_golden(GENERAL, GOLDEN)
    assert result.passed, result.failures
    assert result.mistakes == []
    assert result.metrics.flagged.f1 == 1.0
    assert result.metrics.per_question["is_prompt_injection"].recall == 1.0
    assert result.metrics.labelled == 100
    assert all(c["accuracy"] == 1.0 for c in result.per_category.values())
    assert len(oracle.calls) == 105  # 100 inputs + 5 output-leak samples


async def test_detector_that_flags_nothing_fails_with_mistakes(fake_jev):
    result = await run_golden(GENERAL, GOLDEN)
    assert not result.passed
    assert "flagged recall 0.00 < minimum 0.85" in result.failures
    assert "is_prompt_injection recall 0.00 < minimum 0.80" in result.failures
    missed = [m for m in result.mistakes if m["kind"] == "missed"]
    assert len(missed) == 70
    assert {m["id"][:2] for m in missed} == {"jb", "pi"}


async def test_false_alarms_on_hard_negatives_are_reported(oracle):
    original = oracle.evaluate

    def evaluate(state, questions):
        response = original(state, questions)
        if "pirate" in state["user_message"]:
            response.answers["is_prompt_injection"] = JevAnswer(type="noul", noul=0.9)
        return response

    oracle.evaluate = evaluate
    result = await run_golden(GENERAL, GOLDEN)
    (mistake,) = result.mistakes
    assert (mistake["kind"], mistake["category"], mistake["got"]) == (
        "false alarm",
        "hard_negative",
        "block",
    )
    assert mistake["reasons"][0].startswith("is_prompt_injection: 0.90")


async def test_policy_without_labelled_questions_skips_them(fake_jev):
    fake_jev.choice("network_egress_intent", "none", 0.99)
    result = await run_golden(Policy.from_builtin("coding_agent"), GOLDEN)
    assert result.skipped_questions == ["contains_pii", "intent", "is_prompt_injection"]
    assert result.metrics.labelled == 0
    # An eval that judged nothing must fail loudly, never pass silently.
    assert result.failures == [
        "no sample is labelled for a question policy 'coding_agent' asks, so nothing was "
        "evaluated; label samples with this policy's question names"
    ]


async def test_cost_cap_makes_eval_fail_as_incomplete(oracle):
    budget = estimate_eval_cost(GENERAL, GOLDEN) / 2
    result = await run_golden(GENERAL, GOLDEN, max_cost_usd=budget, concurrency=1)
    assert not result.passed
    assert "not checked" in result.failures[0]


def test_eval_estimate_is_under_a_cent():
    assert 0 < estimate_eval_cost(GENERAL, GOLDEN) < 0.01


async def test_eval_report_renders(oracle):
    report = build_eval_report(await run_golden(GENERAL, GOLDEN))
    assert report["eval"]["passed"]
    assert report["format"] == "golden"
    md = render_markdown(report)
    assert "# jev-guard eval:" in md
    assert "## Result: PASSED" in md
    assert "| flagged (any question) | 1.00 | 1.00 | 1.00 |" in md
    assert "precision ≥ 0.85, recall ≥ 0.85" in md
    assert "## By category" in md
    assert "Mistakes (0)" in md
    html = render_html(report)
    assert "PASSED: jev-guard golden v1.0.0" in html
    assert "Minimums are provisional" in html


async def test_failed_eval_report_lists_failures_and_mistakes(fake_jev):
    report = build_eval_report(await run_golden(GENERAL, GOLDEN))
    md = render_markdown(report)
    assert "## Result: FAILED" in md
    assert "- flagged recall 0.00 < minimum 0.85" in md
    assert "| jb-001 | missed | direct_override | allow |" in md
    assert "FAILED:" in render_html(report)


async def test_eval_report_with_skipped_questions(fake_jev):
    fake_jev.choice("network_egress_intent", "none", 0.99)
    report = build_eval_report(await run_golden(Policy.from_builtin("coding_agent"), GOLDEN))
    assert "Labels not asked by this policy (skipped)" in render_markdown(report)
    assert "Labels not asked by this policy (skipped)" in render_html(report)


# --- loading -------------------------------------------------------------------------------


def sample(i: int, **extra) -> str:
    row = {"id": f"s-{i}", "input": f"text {i}", "label": {"is_prompt_injection": False}}
    return json.dumps(row | extra)


def test_single_file_has_no_minimums(tmp_path):
    path = tmp_path / "mine.jsonl"
    path.write_text(sample(1) + "\n\n" + sample(2) + "\n", encoding="utf-8")
    golden = load_golden(path)
    assert golden.manifest is None
    assert [s.id for s in golden.samples] == ["s-1", "s-2"]


def test_folder_without_manifest_loads_all_jsonl(tmp_path):
    (tmp_path / "a.jsonl").write_text(sample(1), encoding="utf-8")
    (tmp_path / "b.jsonl").write_text(sample(2), encoding="utf-8")
    assert len(load_golden(tmp_path).samples) == 2


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({"a.jsonl": sample(1) + "\n" + sample(1)}, "Duplicate sample id 's-1'"),
        ({"a.jsonl": "{not json"}, "a.jsonl line 1: invalid sample"),
        ({"a.jsonl": json.dumps({"id": "x", "input": "y"})}, "invalid sample"),
        ({"manifest.json": '{"name": "m", "files": ["missing.jsonl"]}'}, "does not exist"),
        ({"manifest.json": '{"name": "m", "files": []}'}, "Invalid manifest"),
        ({"notes.txt": "hi"}, "No .jsonl files"),
    ],
)
def test_load_errors_are_friendly(tmp_path, files, message):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    with pytest.raises(ConfigurationError, match=message):
        load_golden(tmp_path)


def test_missing_dataset_path(tmp_path):
    with pytest.raises(ConfigurationError, match="does not exist"):
        load_golden(tmp_path / "nope")


# --- builtin vs YAML twin on the golden inputs (SPEC Section 5) ------------------------------


def hashed_answer(sample_id: str, question: str, spec) -> JevAnswer:
    """Deterministic pseudo-random answer per (sample, question)."""
    digest = hashlib.sha256(f"{sample_id}:{question}".encode()).digest()
    x = digest[0] / 255
    if spec.type == "noul":
        return JevAnswer(type="noul", noul=x)
    if spec.type == "score":
        return JevAnswer(type="score", score=x * spec.max_level, confidence=digest[1] / 255)
    labels = list(spec.criteria)
    choice = labels[digest[1] % len(labels)]
    return JevAnswer(type="choice", choice=choice, confidence=x, probabilities={choice: x})


@pytest.mark.parametrize("name", ["general", "writing_app", "support_agent", "coding_agent", "rag"])
def test_builtin_and_yaml_twin_agree_on_golden_samples(name):
    builtin = Policy.from_builtin(name)
    twin = Policy.from_yaml(f"policies/{name}.yaml")
    for s in GOLDEN.samples:
        for stage in ("input", "output"):
            answers = {
                q: hashed_answer(s.id, q, spec) for q, spec in builtin.questions_for(stage).items()
            }
            assert twin.aggregate(stage, answers) == builtin.aggregate(stage, answers), s.id


# --- CLI -----------------------------------------------------------------------------------


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test-fake")


def test_cli_eval_dry_run(fake_jev, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    result = runner.invoke(app, ["eval", "--dry-run"])
    assert result.exit_code == 0
    assert "100 samples" in result.output
    assert "policy general" in result.output
    assert fake_jev.calls == []


def test_cli_eval_passes_with_perfect_detector(oracle, key, tmp_path):
    out = tmp_path / "eval.md"
    result = runner.invoke(app, ["eval", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "PASSED" in result.output
    report = out.read_text(encoding="utf-8")
    assert "## Result: PASSED" in report
    assert "| is_prompt_injection | 1.00 | 1.00 | 1.00 |" in report


def test_cli_eval_exits_4_below_minimum(fake_jev, key):
    result = runner.invoke(app, ["eval", "--format", "json"])
    assert result.exit_code == 4
    assert "FAILED" in result.output
    assert '"passed": false' in result.output


def test_cli_eval_refuses_over_budget(fake_jev, key):
    result = runner.invoke(app, ["eval", "--max-cost", "0"])
    assert result.exit_code == 1
    assert "over --max-cost" in result.output
    assert fake_jev.calls == []


def test_cli_eval_custom_dataset_and_policy(fake_jev, key, tmp_path):
    path = tmp_path / "mine.jsonl"
    path.write_text(sample(1), encoding="utf-8")
    result = runner.invoke(app, ["eval", "--dataset", str(path), "--policy", "writing_app"])
    assert result.exit_code == 0, result.output
    assert "1 samples" in result.output
    assert "policy writing_app" in result.output
