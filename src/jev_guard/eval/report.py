"""Scan and eval reports as JSON, Markdown, or a self-contained HTML page (Jinja2).

``build_report`` produces one plain dict; every renderer reads only that dict, so the JSON
report is the complete record and the other formats are views of it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from jev_guard.eval.golden import EvalResult, eval_section
from jev_guard.eval.metrics import Metrics, add_example, worst_action
from jev_guard.eval.scan import ScanResult

TOP_OFFENDERS = 20
SNIPPET_CHARS = 200
LATENCY_BUCKETS_MS = (100, 200, 500, 1000)
_RANK = {"block": 2, "review": 1, "allow": 0}


def _snippet(text: str | None) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= SNIPPET_CHARS else text[: SNIPPET_CHARS - 1] + "…"


def _bucket_label(index: int) -> str:
    if index == 0:
        return f"< {LATENCY_BUCKETS_MS[0]} ms"
    if index == len(LATENCY_BUCKETS_MS):
        return f"≥ {LATENCY_BUCKETS_MS[-1]} ms"
    return f"{LATENCY_BUCKETS_MS[index - 1]}–{LATENCY_BUCKETS_MS[index]} ms"


def _latency_histogram(latencies: list[float]) -> list[dict[str, Any]]:
    counts = [0] * (len(LATENCY_BUCKETS_MS) + 1)
    for ms in latencies:
        index = sum(ms >= edge for edge in LATENCY_BUCKETS_MS)
        counts[index] += 1
    peak = max(counts) or 1
    return [
        {"label": _bucket_label(i), "count": c, "pct": round(100 * c / peak)}
        for i, c in enumerate(counts)
    ]


def build_report(result: ScanResult, *, source: str, log_format: str) -> dict[str, Any]:
    policy = result.policy
    actions = {stage: {"allow": 0, "review": 0, "block": 0} for stage in ("input", "output")}
    latencies: list[float] = []
    tokens = 0
    offenders: list[dict[str, Any]] = []
    metrics = Metrics()
    errors: list[dict[str, Any]] = []

    for item in result.items:
        if item.error:
            errors.append({"line": item.record.line, "error": item.error})
        checked = [v for v in item.verdicts if v is not None]
        for verdict in checked:
            actions[verdict.stage][verdict.action] += 1
            if verdict.latency_ms > 0:
                latencies.append(verdict.latency_ms)
            tokens += verdict.input_tokens_used
            if verdict.action != "allow":
                text = item.record.input if verdict.stage == "input" else item.record.output
                offenders.append(
                    {
                        "line": item.record.line,
                        "stage": verdict.stage,
                        "action": verdict.action,
                        "confidence": round(verdict.confidence, 4),
                        "reasons": verdict.reasons,
                        "text": _snippet(text),
                    }
                )
        if item.record.label is not None and checked:
            add_example(metrics, policy, item.record.label, item.verdicts)

    offenders.sort(key=lambda o: (-_RANK[o["action"]], o["confidence"]))
    checked_records = sum(1 for i in result.items if any(i.verdicts))
    flagged_records = sum(1 for i in result.items if worst_action(i.verdicts) != "allow")
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "format": log_format,
        "policy": {"name": policy.name, "version": policy.version},
        "summary": {
            "records": len(result.items),
            "checked": checked_records,
            "flagged": flagged_records,
            "skipped_budget": sum(1 for i in result.items if i.skipped),
            "errors": len(errors),
            "checks": len(latencies),
            "input_tokens": tokens,
            "cost_usd": round(result.cost_usd, 6),
            "avg_latency_ms": round(avg_latency, 1),
            "duration_s": round(result.duration_s, 2),
            "budget_exhausted": result.budget_exhausted,
        },
        "actions": actions,
        "latency_histogram": _latency_histogram(latencies),
        "top_offenders": offenders[:TOP_OFFENDERS],
        "metrics": metrics.as_dict() if metrics.labelled else None,
        "errors": errors[:50],
        "warnings": result.warnings[:50],
    }


# --- renderers ----------------------------------------------------------------------------


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    ev = report.get("eval")
    lines = [
        f"# jev-guard {'eval' if ev else 'scan'}: {report['source']}",
        "",
        f"Policy `{report['policy']['name']}` v{report['policy']['version']} · "
        f"format `{report['format']}` · {report['generated_at']}",
        "",
        f"**{s['records']}** records, **{s['flagged']}** flagged, "
        f"**${s['cost_usd']:.4f}** for {s['checks']} checks "
        f"({s['input_tokens']:,} tokens), avg {s['avg_latency_ms']:.0f} ms per check.",
        "",
        "> Latency is wall time per check while checks run concurrently, so it reflects how "
        "loaded the backend was, not what one check costs on its own.",
    ]
    if s["budget_exhausted"]:
        lines += ["", f"> Stopped at the cost cap: {s['skipped_budget']} records not checked."]
    if ev:
        lines += _markdown_eval_header(ev)
        if report["metrics"]:
            lines += _markdown_metrics(report["metrics"], ev["minimums"])
        lines += _markdown_eval_details(ev)
        return "\n".join(lines) + "\n"
    lines += ["", "## Actions", "", "| stage | allow | review | block |", "|---|---:|---:|---:|"]
    for stage, counts in report["actions"].items():
        lines.append(f"| {stage} | {counts['allow']} | {counts['review']} | {counts['block']} |")
    lines += ["", "## Latency", "", "| bucket | checks |", "|---|---:|"]
    lines += [f"| {b['label']} | {b['count']} |" for b in report["latency_histogram"]]
    lines += ["", f"## Top {TOP_OFFENDERS} offenders", ""]
    if report["top_offenders"]:
        lines += [
            "| line | stage | action | confidence | reasons | text |",
            "|---:|---|---|---:|---|---|",
        ]
        for o in report["top_offenders"]:
            reasons = _md_escape("; ".join(o["reasons"]))
            lines.append(
                f"| {o['line']} | {o['stage']} | {o['action']} | {o['confidence']:.2f} | "
                f"{reasons} | {_md_escape(o['text'])} |"
            )
    else:
        lines.append("Nothing was flagged.")
    if report["metrics"]:
        lines += _markdown_metrics(report["metrics"])
    if report["errors"]:
        lines += ["", "## Errors", ""] + [
            f"- line {e['line']}: {e['error']}" for e in report["errors"]
        ]
    return "\n".join(lines) + "\n"


def metric_rows(
    metrics: dict[str, Any], minimums: dict[str, dict[str, float]] | None = None
) -> list[dict[str, Any]]:
    """One row per question (``flagged`` first) with its metrics and any manifest minimums."""
    minimums = minimums or {}
    rows = [("flagged", metrics["flagged"]), *metrics["per_question"].items()]
    return [
        {
            "name": "flagged (any question)" if name == "flagged" else name,
            **m,
            "minimum": ", ".join(f"{k} ≥ {v:.2f}" for k, v in minimums.get(name, {}).items()),
        }
        for name, m in rows
    ]


def _markdown_metrics(
    metrics: dict[str, Any], minimums: dict[str, dict[str, float]] | None = None
) -> list[str]:
    lines = [
        "",
        f"## Accuracy on {metrics['labelled']} labelled records",
        "",
        "| question | precision | recall | F1 | support | minimum |",
        "|---|---:|---:|---:|---:|---|",
    ]
    lines += [
        f"| {r['name']} | {r['precision']:.2f} | {r['recall']:.2f} | {r['f1']:.2f} | "
        f"{r['support']} | {r['minimum']} |"
        for r in metric_rows(metrics, minimums)
    ]
    return lines


def _markdown_eval_header(ev: dict[str, Any]) -> list[str]:
    verdict = "PASSED" if ev["passed"] else "FAILED"
    lines = ["", f"## Result: {verdict}", ""]
    lines += [f"- {failure}" for failure in ev["failures"]] or ["All minimums met."]
    if ev["provisional"]:
        lines += ["", "> Minimums are provisional until measured against real Jev."]
    if ev["skipped_questions"]:
        skipped = ", ".join(ev["skipped_questions"])
        lines += ["", f"Labels not asked by this policy (skipped): {skipped}"]
    return lines


def _markdown_eval_details(ev: dict[str, Any]) -> list[str]:
    lines = ["", "## By category", "", "| category | samples | correct | accuracy |"]
    lines += ["|---|---:|---:|---:|"]
    lines += [
        f"| {name} | {c['samples']} | {c['correct']} | {c['accuracy']:.0%} |"
        for name, c in ev["per_category"].items()
    ]
    lines += ["", f"## Mistakes ({len(ev['mistakes'])})", ""]
    if not ev["mistakes"]:
        return [*lines, "None."]
    lines += ["| id | kind | category | got | reasons | text |", "|---|---|---|---|---|---|"]
    lines += [
        f"| {m['id']} | {m['kind']} | {m['category']} | {m['got']} | "
        f"{_md_escape('; '.join(m['reasons']))} | {_md_escape(m['text'])} |"
        for m in ev["mistakes"]
    ]
    return lines


_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>jev-guard {{ kind }}: {{ r.source }}</title>
<style>
:root { --bg:#fff; --fg:#1b1f24; --muted:#5b6470; --line:#e3e6ea; --card:#f6f8fa;
        --allow:#1a7f37; --review:#9a6700; --block:#cf222e; --bar:#0969da; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --line:#30363d; --card:#161b22;
          --allow:#3fb950; --review:#d29922; --block:#f85149; --bar:#4493f8; } }
* { box-sizing: border-box; }
body { margin:0; padding:24px 16px; background:var(--bg); color:var(--fg);
       font:15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; word-break: break-all; }
h2 { font-size: 17px; margin: 32px 0 12px; }
.muted { color: var(--muted); }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-top:20px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px 14px; }
.card b { display:block; font-size:22px; font-variant-numeric: tabular-nums; }
.warn { border-color: var(--review); }
.result { margin-top:20px; padding:14px 16px; border-radius:8px; border:2px solid currentColor; }
.result h2 { margin:0 0 6px; }
.result ul { margin:6px 0 0; }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:14px; }
th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; }
td.num, th.num { text-align:right; font-variant-numeric: tabular-nums; }
.pill { display:inline-block; padding:1px 8px; border-radius:99px; font-size:12px; font-weight:600;
        border:1px solid currentColor; }
.allow { color:var(--allow); } .review { color:var(--review); } .block { color:var(--block); }
.bar { height:10px; background:var(--bar); border-radius:3px; min-width:2px; }
ul.reasons { margin:0; padding-left:18px; }
code { font-size: 13px; }
</style>
</head>
<body><main>
<h1>jev-guard {{ kind }}</h1>
<div class="muted"><code>{{ r.source }}</code> · policy <b>{{ r.policy.name }}</b> v{{ r.policy.version }}
 · format {{ r.format }} · {{ r.generated_at }}</div>

<div class="cards">
  <div class="card">Records<b>{{ r.summary.records }}</b></div>
  <div class="card">Flagged<b>{{ r.summary.flagged }}</b></div>
  <div class="card">Cost<b>${{ "%.4f"|format(r.summary.cost_usd) }}</b>
    <span class="muted">{{ "{:,}".format(r.summary.input_tokens) }} tokens</span></div>
  <div class="card">Avg latency<b>{{ "%.0f"|format(r.summary.avg_latency_ms) }} ms</b>
    <span class="muted">{{ r.summary.checks }} checks, run concurrently</span></div>
  {% if r.summary.budget_exhausted %}<div class="card warn">Stopped at cost cap
    <b>{{ r.summary.skipped_budget }}</b><span class="muted">records not checked</span></div>{% endif %}
  {% if r.summary.errors %}<div class="card warn">Errors<b>{{ r.summary.errors }}</b></div>{% endif %}
</div>

{% if ev %}
<div class="result {{ 'allow' if ev.passed else 'block' }}">
<h2>{{ "PASSED" if ev.passed else "FAILED" }}: {{ ev.dataset }}{% if ev.dataset_version %} v{{ ev.dataset_version }}{% endif %}</h2>
{% if ev.failures %}<ul>{% for f in ev.failures %}<li>{{ f }}</li>{% endfor %}</ul>
{% else %}<span>All minimums met on {{ ev.samples }} samples.</span>{% endif %}
</div>
{% if ev.provisional %}<p class="muted">Minimums are provisional until measured against real Jev.</p>{% endif %}
{% if ev.skipped_questions %}<p class="muted">Labels not asked by this policy (skipped): {{ ev.skipped_questions|join(", ") }}</p>{% endif %}
{% endif %}

<h2>Actions</h2>
<div class="scroll"><table>
<tr><th>stage</th><th class="num">allow</th><th class="num">review</th><th class="num">block</th></tr>
{% for stage, c in r.actions.items() %}
<tr><td>{{ stage }}</td><td class="num allow">{{ c.allow }}</td>
<td class="num review">{{ c.review }}</td><td class="num block">{{ c.block }}</td></tr>
{% endfor %}
</table></div>

<h2>Latency</h2>
<div class="scroll"><table>
{% for b in r.latency_histogram %}
<tr><td style="width:120px">{{ b.label }}</td><td class="num" style="width:60px">{{ b.count }}</td>
<td><div class="bar" style="width:{{ b.pct }}%"></div></td></tr>
{% endfor %}
</table></div>

<h2>Top offenders</h2>
{% if r.top_offenders %}
<div class="scroll"><table>
<tr><th class="num">line</th><th>stage</th><th>action</th><th class="num">confidence</th><th>reasons</th><th>text</th></tr>
{% for o in r.top_offenders %}
<tr><td class="num">{{ o.line }}</td><td>{{ o.stage }}</td>
<td><span class="pill {{ o.action }}">{{ o.action }}</span></td>
<td class="num">{{ "%.2f"|format(o.confidence) }}</td>
<td><ul class="reasons">{% for reason in o.reasons %}<li><code>{{ reason }}</code></li>{% endfor %}</ul></td>
<td>{{ o.text }}</td></tr>
{% endfor %}
</table></div>
{% else %}<p class="muted">Nothing was flagged.</p>{% endif %}

{% if r.metrics %}
<h2>Accuracy on {{ r.metrics.labelled }} labelled records</h2>
<div class="scroll"><table>
<tr><th>question</th><th class="num">precision</th><th class="num">recall</th><th class="num">F1</th><th class="num">support</th>{% if ev %}<th>minimum</th>{% endif %}</tr>
{% for m in rows %}
<tr><td>{{ m.name }}</td><td class="num">{{ "%.2f"|format(m.precision) }}</td><td class="num">{{ "%.2f"|format(m.recall) }}</td>
<td class="num">{{ "%.2f"|format(m.f1) }}</td><td class="num">{{ m.support }}</td>{% if ev %}<td class="muted">{{ m.minimum }}</td>{% endif %}</tr>
{% endfor %}
</table></div>
{% endif %}

{% if ev %}
<h2>By category</h2>
<div class="scroll"><table>
<tr><th>category</th><th class="num">samples</th><th class="num">correct</th><th class="num">accuracy</th></tr>
{% for name, c in ev.per_category.items() %}
<tr><td>{{ name }}</td><td class="num">{{ c.samples }}</td><td class="num">{{ c.correct }}</td>
<td class="num">{{ "%.0f"|format(c.accuracy * 100) }}%</td></tr>
{% endfor %}
</table></div>

<h2>Mistakes ({{ ev.mistakes|length }})</h2>
{% if ev.mistakes %}
<div class="scroll"><table>
<tr><th>id</th><th>kind</th><th>category</th><th>got</th><th>reasons</th><th>text</th></tr>
{% for m in ev.mistakes %}
<tr><td><code>{{ m.id }}</code></td><td>{{ m.kind }}</td><td>{{ m.category }}</td>
<td><span class="pill {{ m.got }}">{{ m.got }}</span></td>
<td><ul class="reasons">{% for reason in m.reasons %}<li><code>{{ reason }}</code></li>{% endfor %}</ul></td>
<td>{{ m.text }}</td></tr>
{% endfor %}
</table></div>
{% else %}<p class="muted">None.</p>{% endif %}
{% endif %}

{% if r.errors %}<h2>Errors</h2><ul>{% for e in r.errors %}<li>line {{ e.line }}: {{ e.error }}</li>{% endfor %}</ul>{% endif %}
{% if r.warnings %}<h2>Warnings</h2><ul class="muted">{% for w in r.warnings %}<li>{{ w }}</li>{% endfor %}</ul>{% endif %}
</main></body>
</html>
"""


def render_html(report: dict[str, Any]) -> str:
    try:
        import jinja2  # noqa: PLC0415 (optional dependency)
    except ImportError as err:
        from jev_guard.errors import ConfigurationError  # noqa: PLC0415

        raise ConfigurationError(
            "HTML reports need Jinja2.",
            hint='pip install "jev-guard[cli]", or use --format json / md',
        ) from err
    env = jinja2.Environment(autoescape=True, undefined=jinja2.StrictUndefined)
    ev = report.get("eval")
    rows = (
        metric_rows(report["metrics"], ev["minimums"] if ev else None) if report["metrics"] else []
    )
    return env.from_string(_HTML).render(r=report, ev=ev, rows=rows, kind="eval" if ev else "scan")


def build_eval_report(result: EvalResult) -> dict[str, Any]:
    """A scan report over the golden samples, plus the eval verdict under ``"eval"``."""
    report = build_report(result.scan, source=result.golden.source, log_format="golden")
    report["eval"] = eval_section(result)
    return report


RENDERERS = {"json": render_json, "md": render_markdown, "html": render_html}
