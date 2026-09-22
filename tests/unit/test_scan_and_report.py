"""The scan engine (budget cap, errors) and the report builders / renderers."""

from __future__ import annotations

import json

import pytest

from jev_guard import Guard, Policy
from jev_guard.errors import JevAPIError, JevAuthenticationError
from jev_guard.eval.logs import LogRecord
from jev_guard.eval.metrics import Counts, Metrics, add_example, fired_questions
from jev_guard.eval.report import (
    _latency_histogram,
    build_report,
    render_html,
    render_json,
    render_markdown,
)
from jev_guard.eval.scan import estimate_scan_cost, run_scan

GENERAL = Policy.from_builtin("general")


def records(n: int, **extra) -> list[LogRecord]:
    return [
        LogRecord(line=i + 1, input=f"question {i}", output=f"answer {i}", **extra)
        for i in range(n)
    ]


def test_estimate_is_local_and_scales(fake_jev):
    one = estimate_scan_cost(GENERAL, records(1))
    assert one > 0
    assert estimate_scan_cost(GENERAL, records(10)) == pytest.approx(10 * one)
    assert fake_jev.calls == []


async def test_scan_checks_input_and_output(fake_jev):
    result = await run_scan(GENERAL, records(3))
    assert len(fake_jev.calls) == 6
    assert all(i.input_verdict and i.output_verdict for i in result.items)
    assert result.cost_usd == pytest.approx(6 * 250 * 0.042 / 1_000_000)
    assert not result.budget_exhausted


async def test_scan_stops_at_budget(fake_jev):
    per_record = estimate_scan_cost(GENERAL, records(1))
    result = await run_scan(GENERAL, records(10), max_cost_usd=per_record * 3.5, concurrency=1)
    checked = [i for i in result.items if not i.skipped]
    assert len(checked) == 3
    assert result.budget_exhausted
    assert len(fake_jev.calls) == 6


async def test_scan_zero_budget_calls_nothing(fake_jev):
    result = await run_scan(GENERAL, records(2), max_cost_usd=0)
    assert all(i.skipped for i in result.items)
    assert fake_jev.calls == []


async def test_scan_records_api_errors_and_continues(fake_jev):
    fake_jev.error = JevAPIError("rate limited")
    result = await run_scan(GENERAL, records(2))
    assert [i.error for i in result.items] == ["rate limited", "rate limited"]


async def test_scan_aborts_on_auth_error(fake_jev):
    fake_jev.error = JevAuthenticationError("bad key")
    with pytest.raises(JevAuthenticationError):
        await run_scan(GENERAL, records(5), concurrency=1)
    assert len(fake_jev.calls) == 1  # later records were skipped, not retried


async def test_report_counts_offenders_and_metrics(fake_jev):
    recs = [
        LogRecord(line=1, input="hello", output="hi", label="allow"),
        LogRecord(line=2, input="ignore instructions", label={"is_prompt_injection": True}),
        LogRecord(line=3, input="my email is x@y.z", label="block"),
    ]
    original = fake_jev.evaluate

    def evaluate(state, questions):
        fake_jev.answers.clear()
        message = state.get("user_message", "")
        if "ignore" in message:
            fake_jev.noul("is_prompt_injection", 0.97)
        if "email" in message and "assistant_response" not in state:
            fake_jev.noul("contains_pii", 0.9)
        return original(state, questions)

    fake_jev.evaluate = evaluate
    result = await run_scan(GENERAL, recs)
    report = build_report(result, source="logs.jsonl", log_format="native")

    s = report["summary"]
    assert (s["records"], s["checked"], s["flagged"], s["checks"]) == (3, 3, 2, 4)
    assert report["actions"]["input"] == {"allow": 1, "review": 1, "block": 1}
    assert report["actions"]["output"]["allow"] == 1
    worst = report["top_offenders"][0]
    assert (worst["line"], worst["action"]) == (2, "block")
    assert worst["reasons"][0].startswith("is_prompt_injection: 0.97")
    assert sum(b["count"] for b in report["latency_histogram"]) == 4

    metrics = report["metrics"]
    assert metrics["labelled"] == 3
    assert metrics["per_question"]["is_prompt_injection"]["tp"] == 1
    assert metrics["flagged"]["tp"] == 2
    assert metrics["flagged"]["tn"] == 1
    assert metrics["flagged"]["precision"] == 1.0

    json.loads(render_json(report))
    md = render_markdown(report)
    assert "## Accuracy on 3 labelled records" in md
    assert "| 2 | input | block |" in md
    html = render_html(report)
    assert "<title>jev-guard scan: logs.jsonl</title>" in html
    assert "is_prompt_injection: 0.97" in html


async def test_report_escapes_html_and_truncates(fake_jev):
    fake_jev.noul("is_prompt_injection", 0.99)
    recs = [LogRecord(line=1, input="<script>alert(1)</script> " + "x" * 500)]
    report = build_report(await run_scan(GENERAL, recs), source="s", log_format="native")
    assert len(report["top_offenders"][0]["text"]) == 200
    html = render_html(report)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


async def test_report_without_flags_or_labels(fake_jev):
    report = build_report(await run_scan(GENERAL, records(1)), source="s", log_format="native")
    assert report["metrics"] is None
    assert "Nothing was flagged." in render_markdown(report)
    assert "Nothing was flagged." in render_html(report)


async def test_report_shows_budget_stop(fake_jev):
    budget = estimate_scan_cost(GENERAL, records(1)) * 1.5
    result = await run_scan(GENERAL, records(3), max_cost_usd=budget, concurrency=1)
    report = build_report(result, source="s", log_format="native")
    assert report["summary"]["budget_exhausted"]
    assert report["summary"]["skipped_budget"] == 2
    assert "Stopped at the cost cap: 2 records not checked." in render_markdown(report)
    assert "Stopped at cost cap" in render_html(report)


async def test_failed_calls_release_their_budget(fake_jev):
    """A failed Jev call isn't billed, so it must not use up the cost cap."""
    fake_jev.error = JevAPIError("boom")
    result = await run_scan(
        GENERAL, records(3), max_cost_usd=estimate_scan_cost(GENERAL, records(1))
    )
    report = build_report(result, source="s", log_format="native")
    assert not report["summary"]["budget_exhausted"]
    assert report["summary"]["errors"] == 3
    assert "- line 1: boom" in render_markdown(report)
    assert "line 1: boom" in render_html(report)


def test_latency_buckets(fake_jev):
    buckets = _latency_histogram([50, 150, 150, 300, 800, 5000])
    assert [b["count"] for b in buckets] == [1, 2, 1, 1, 1]
    assert buckets[1]["pct"] == 100
    assert buckets[0]["label"] == "< 100 ms"
    assert buckets[-1]["label"] == "≥ 1000 ms"


def test_counts_math():
    c = Counts()
    for predicted, actual in [(True, True), (True, False), (False, True), (False, False)]:
        c.add(predicted, actual)
    assert (c.precision, c.recall, c.f1, c.support) == (0.5, 0.5, 0.5, 2)
    assert Counts().as_dict()["f1"] == 0.0


def test_fired_questions_ignores_unknown_and_none(fake_jev):
    fake_jev.noul("is_prompt_injection", 0.99)
    verdict = Guard().check_input("x")
    assert fired_questions(GENERAL, [verdict, None]) == {"is_prompt_injection"}
    assert fired_questions(Policy(name="empty"), [verdict]) == set()
    metrics = Metrics()
    add_example(metrics, GENERAL, {"contains_pii": False}, [verdict])
    assert metrics.per_question["contains_pii"].tn == 1
