# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-09-22

First release.

### Added

- `Guard` with `check_input` / `check_output` and async `acheck_input` / `acheck_output`,
  returning a JSON-serializable `Verdict` (action, reasons, confidence, raw Jev answers,
  latency, tokens, cost, suggested safe reply).
- Five builtin policies: `general` (default), `writing_app`, `support_agent`,
  `coding_agent`, `rag`, each with a YAML twin in `policies/` and sample `strict`,
  `permissive`, and `coding_agent_prod` policies.
- Severity-weighted aggregation: critical questions block alone, high questions sum
  toward review and block thresholds, medium and low only lower confidence. Every
  reason names the question, value, threshold, and severity.
- YAML policies with `extends`, per-question merging, and `null` removal, plus a
  validator that reports every problem with line numbers and "did you mean" hints.
- Streaming guards (`Guard.astream_check`): buffer-and-check (default) and rollback
  strategies, a `StreamCut` marker for stopped streams, and support for OpenAI, Anthropic,
  and LangChain chunks.
- Integrations: `@guarded` decorator, `wrap_openai` (Chat Completions and Responses, sync
  and async, including streaming), `wrap_anthropic`, a LangChain callback handler that
  passes retrieved documents to the rag policy, and a LiteLLM proxy guardrail.
- OpenTelemetry spans for every check (`setup_telemetry()`), with a session span for
  `with Guard(...)`.
- `redact()` / `aredact()`: regex redaction of emails, phone numbers, SSNs, and
  Luhn-checked card numbers, plus a strict mode that asks Jev about each sentence.
- `jev-guard` CLI: `check`, `scan` (native, Langfuse, and Arize JSONL; HTML, JSON, and
  Markdown reports), `eval`, and `policy list/show/validate`, with cost estimates,
  `--dry-run`, and `--max-cost` caps.
- Pluggable backends (`jev_guard.backends`): Jev (default), local transformer models
  (offline and free), an LLM judge on any OpenAI-compatible server or Anthropic, and
  fallback chains like `jev,local`. Selected process-wide with `set_backend()`,
  `JEV_GUARD_BACKEND`, or `jev-guard --backend`; policies, verdicts, CLI, and integrations
  are unchanged.
- Scale: an answer cache (on by default), client-side rate limiting under TypeSafe's
  1,200/min, `jev_guard.parallel.run_with_guard` to check input while the LLM generates
  (zero added latency), and `ToolGuard(skip_calls=..., skip_results=...)`.
- Agent guarding: `jev_guard.agents.ToolGuard` checks tool calls before they run and tool
  results before the model reads them (windowed for long results), with a new builtin
  `agent_tools` policy. `wrap_openai` / `wrap_anthropic` take `tool_policy=` to check every
  tool call in a response.
- Multi-turn checks: `jev_guard.conversation.check_conversation` catches jailbreaks spread
  across several messages; system prompts are never sent.
- `jev-guard hook claude-code`: a Claude Code PreToolUse / PostToolUse hook (blocks, asks for
  review, or stays silent), failing open or closed.
- A benchmark harness (`python -m benchmarks.run`) comparing jev-guard with a regex
  baseline, an LLM judge, and LLM Guard on the golden set and three public, openly licensed
  prompt-injection datasets. Free by default, cost-capped with `--run`.
- `SECURITY.md`, `CONTRIBUTING.md`, and issue templates, including one for reporting missed
  attacks and false alarms.
- A 100-sample golden eval dataset (50 jailbreaks across 9 attack patterns, 30 clean
  messages including 15 hard negatives, 20 synthetic-PII samples) shipped in the package.

[0.1.0]: https://github.com/rudra72r/jev-guard/releases/tag/v0.1.0
