# FAQ

## Does it break streaming?

No. See [Streaming](streaming.md). Buffer mode never shows unchecked text. Rollback mode keeps time-to-first-token and retracts if needed. The client wrappers guard `stream=True` and keep the SDK's chunk objects.

## How accurate is it?

v0.1 ships a 100-sample labelled eval (`jev-guard eval`) with the pass bar each builtin must clear.

Measured on it with the **offline** backend and `general_local`: flagged F1 0.94, `is_prompt_injection` F1 0.95, `contains_pii` F1 0.92, `intent` F1 0.86. A regex baseline on the same samples catches 46% of the attacks. Reproduce with `jev-guard --backend local eval --policy general_local` — no key, no cost.

Numbers for Jev itself aren't published yet, because they haven't been measured on a real account. Run `jev-guard eval` on your key (about $0.001) and you'll have them for your setup. For your own traffic, add labels to a log sample and run `jev-guard scan`.

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

To keep running instead, set a fallback chain: `JEV_GUARD_BACKEND=jev,local` tries Jev first and falls back to local models when it's unreachable, throttled, or missing a key. The verdict records which backend answered.

## How does it compare to Guardrails AI, NeMo Guardrails, and LLM Guard?

- **[Guardrails AI](https://github.com/guardrails-ai/guardrails)**: a framework of validators, with a hub of community validators (regex, local models, LLM-based), centred on validating and correcting structured LLM output.
- **[NeMo Guardrails](https://github.com/NVIDIA/NeMo-Guardrails)**: NVIDIA's toolkit for programming conversational rails in Colang. Rails are usually evaluated with LLM calls.
- **[LLM Guard](https://github.com/protectai/llm-guard)**: Protect AI's library of input and output scanners, many backed by local transformer models you host yourself.
- **jev-guard**: asks several typed questions in one call and turns the probabilities into an explained allow / review / block, with YAML policies, a labelled eval, and a choice of backend (Jev, local models, or an LLM judge).

They can be combined. Use jev-guard where per-check speed, cost, and an explainable verdict matter, and the others for structure validation or dialogue flows.

## Can I run it without sending data to TypeSafe?

Yes. `JEV_GUARD_BACKEND=local` answers every question with transformer models on your own machine, and nothing leaves it — same policies, same verdicts, no key and no cost. It's slower (~7 s a check on a laptop CPU, after a one-time ~23 s model load) and a little less accurate, so [retune the thresholds](backends.md). `redact(text, level="fast")` is pure regex and never calls anything.

On the default Jev backend, the text you check goes to `api.typesafe.ai` (see [TypeSafe's legal page](https://docs.typesafe.ai/legal)) and nowhere else. See [Backends](backends.md) for everything in between, including Jev via Cloudflare and an LLM judge on your own server.

## Who pays for Jev when I use jev-guard?

You do, with your own `TYPESAFE_API_KEY`. The library has no key of its own, and the project's CI never calls Jev.
