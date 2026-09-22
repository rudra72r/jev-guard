# Batch scanning Langfuse traces

Before turning guardrails on in production, run them over last week's traffic to see what they would have flagged and what it would have cost.

## 1. Export traces

In Langfuse, export traces (or generations) as JSONL. jev-guard recognises the export from its keys (`traceId`, `projectId`, `sessionId`). It reads `input` and `output` whether they are strings, chat-message lists, or `{"messages": [...]}` objects, and picks up retrieved documents from `metadata.context`.

## 2. Estimate, then scan

```bash
jev-guard scan traces.jsonl --policy support_agent --dry-run
# N records (langfuse format), M checks with policy support_agent: estimated $X
# (roughly $0.000037 per conversation for support_agent; see Cost)

jev-guard scan traces.jsonl --policy support_agent --out report.html --max-cost 1.50
```

The report has counts by action for inputs and outputs, the 20 worst offenders with their reasons, total cost, and a latency histogram. `--format json` gives the full data for your own analysis. `--format md` gives a Markdown summary you can paste into a ticket.

## 3. Measure accuracy on a labelled sample

Label a few hundred records by hand, then add a `label` field to each line: an action (`"allow"`, `"review"`, `"block"`) or per question (`{"contains_refund_promise": true}`). The report then shows precision, recall, and F1 for each question. That's the evidence for changing thresholds.

## Arize and others

Arize and OpenInference span exports are detected the same way, flat (`attributes.input.value`) or nested. For anything else, convert to the native shape:

```json
{"input": "user message", "output": "assistant reply", "context": ["doc"], "label": "allow"}
```

Export formats change between versions of both tools. If a record can't be read, it's skipped and listed with its line number in the report's warnings.

## From Python

`examples/05_batch_scan.py` shows the same flow without the CLI: `read_log_file`, `estimate_scan_cost`, `run_scan`, `build_report`.
