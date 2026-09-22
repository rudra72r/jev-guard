# FAQ

## Does it break streaming?

No. See [Streaming](streaming.md). Buffer mode never shows unchecked text. Rollback mode keeps time-to-first-token and retracts if needed. The client wrappers guard `stream=True` and keep the SDK's chunk objects.

## How accurate is it?

v0.1 ships a 100-sample labelled eval (`jev-guard eval`) with the pass bar each builtin must clear. Published numbers will follow the first full run against Jev. Until then, run it yourself: it takes a minute and costs about $0.001. For your own traffic, add labels to a log sample and run `jev-guard scan`.

## How do I tune thresholds?

`Policy.from_builtin(...).override(thresholds={...})`, or a YAML file with `extends:`. Then measure with `eval` or a labelled `scan` and read the report's mistakes list. See [Policies](policies.md#tuning).

## Is it deterministic?

The rules that turn answers into actions are. Jev's answers are probabilities, so a borderline input can land on either side of a threshold. Pin the model version (`TYPESAFE_DEFAULT_MODEL=jev-1.13.0`) so a model upgrade doesn't shift results without you noticing, and re-run `eval` when you upgrade.

## Does it support other languages?

v0.1 is designed and tested for English only. Jev may work in other languages, but jev-guard's questions and eval haven't been checked on them.

## Can I cap what it costs?

`scan` and `eval` refuse to start above `--max-cost` and stop if actual spend reaches it. In an app, each check's cost is small and bounded by the text size; the `Verdict` reports it, and the [telemetry](telemetry.md) span carries `cost.usd` for dashboards and alerts.

## What happens when Jev is slow or down?

The SDK retries rate limits and overload responses with backoff. After that, checks raise `JevAPIError` (or the more specific `JevRateLimitError`, `JevUnavailableError`, or `JevAuthenticationError`). jev-guard never decides to fail open or closed for you.

## How does it compare to Guardrails AI, NeMo Guardrails, and LLM Guard?

- **[Guardrails AI](https://github.com/guardrails-ai/guardrails)**: a framework of validators, with a hub of community validators (regex, local models, LLM-based), centred on validating and correcting structured LLM output.
- **[NeMo Guardrails](https://github.com/NVIDIA/NeMo-Guardrails)**: NVIDIA's toolkit for programming conversational rails in Colang. Rails are usually evaluated with LLM calls.
- **[LLM Guard](https://github.com/protectai/llm-guard)**: Protect AI's library of input and output scanners, many backed by local transformer models you host yourself.
- **jev-guard**: asks several typed questions in one Jev call and turns the probabilities into an explained allow / review / block, with YAML policies and a labelled eval.

They can be combined. Use jev-guard where per-check speed and cost matter, and the others for structure validation, dialogue flows, or fully offline scanning.

## Can I run it without sending data to TypeSafe?

No. Jev is a hosted model. The text you check goes to `api.typesafe.ai` (see [TypeSafe's legal page](https://docs.typesafe.ai/legal)); nothing goes anywhere else. Masking with `redact(text, level="fast")` first is local and free.

## Who pays for Jev when I use jev-guard?

You do, with your own `TYPESAFE_API_KEY`. The library has no key of its own, and the project's CI never calls Jev.
