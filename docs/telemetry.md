# Telemetry

```bash
pip install "jev-guard[otel]"
```

```python
from jev_guard.telemetry import setup_telemetry

setup_telemetry()
```

After that one line, every check that calls Jev emits an OpenTelemetry span named `jev_guard.check`. The spans go to whatever tracer provider and exporter your app has configured (Langfuse, Arize, Honeycomb, Jaeger, Sentry, …). With none configured, OpenTelemetry drops them at no cost. Setting `JEV_GUARD_TELEMETRY_ENABLED=1` does the same without code.

## Span attributes

| attribute | example |
|---|---|
| `policy.name` | `support_agent` |
| `policy.version` | `1.0.0` |
| `guard.stage` | `input` or `output` |
| `verdict.action` | `block` |
| `verdict.confidence` | `0.41` |
| `verdict.blocked` | `true` |
| `verdict.reasons` | `contains_sla_commitment: 0.91 > 0.70 (critical)` (comma-joined) |
| `latency.ms` | `184.2` |
| `tokens.input` | `412` |
| `cost.usd` | `0.0000173` |
| `jev.model` | `jev-1.13.0` |

Failed Jev calls mark the span as an error and record the exception. Checks skipped without calling Jev (empty text) emit no span.

## Grouping a request's checks

```python
with Guard(policy="rag") as g:
    v_in = g.check_input(query)
    answer = call_llm(query)
    v_out = g.check_output(query, answer, context=docs)
```

The `with` block opens a `jev_guard.session` span, and both checks appear as its children, so one request shows up as one trace.

## Audit logs without OpenTelemetry

Every `Verdict` serializes to JSON:

```python
log.info("guard", extra={"verdict": v.model_dump()})
```

It includes the policy name and version, each question's raw answer, the reasons, the tokens, and the cost.
