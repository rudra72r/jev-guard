# Backends

A backend is whatever answers a policy's questions. Jev is the default, but the policies,
verdicts, CLI, integrations and telemetry work the same on any of them, so you can run
offline, or keep working when Jev is unreachable.

```python
from jev_guard import backends

backends.set_backend("local")  # process-wide; every Guard picks it up
```

No code change needed either way:

```bash
export JEV_GUARD_BACKEND=jev,local
jev-guard --backend local eval
```

## The options

| backend | spec | needs | speed | cost | notes |
|---|---|---|---|---|---|
| **Jev** (default) | `jev`, `jev:jev-1.13.0` | `TYPESAFE_API_KEY` | 70–500 ms | $0.042 / 1M tokens | Best quality per millisecond. Doesn't generate text, so it can't be talked into ignoring its instructions. |
| **Local** | `local`, `local:MODEL` | `pip install "jev-guard[local]"` | ~6 s per check on a 4-thread CPU (measured), much faster on GPU | free | Fully offline and private. Less nuanced. |
| **LLM judge** | `openai:MODEL`, `ollama:MODEL`, `openai-compatible:MODEL@URL`, `anthropic:MODEL` | that provider's key (none for local servers) | 0.3–3 s | the model's price | Works with any OpenAI-compatible server: Ollama, vLLM, LM Studio, llama.cpp, OpenRouter. |
| **Jev via Cloudflare** | `cloudflare` | `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | same as Jev | Cloudflare's price | The same model, billed to Cloudflare. **No TypeSafe account needed**, which matters while TypeSafe signups are closed. |
| **Fallback** | `jev,local` | — | — | — | Tries each in turn when one is unreachable, throttled, or has no key. |

Everything gets an in-memory cache, and Jev gets client-side rate limiting (see
[Running at scale](scale.md)).

## Offline and private

```bash
pip install "jev-guard[local]"
export JEV_GUARD_BACKEND=local
```

Nothing leaves the machine. Two models do the work: a dedicated prompt-injection classifier
(`protectai/deberta-v3-base-prompt-injection-v2`, Apache-2.0) answers the injection
questions, and a zero-shot NLI model (`MoritzLaurer/deberta-v3-base-zeroshot-v2.0`, MIT)
answers everything else, including choice and score questions. They download on first use
(~1.5 GB).

For roughly 3x the speed at some cost in accuracy:
`local:MoritzLaurer/deberta-v3-xsmall-zeroshot-v1.1-all-33`.

Measure the tradeoff on the bundled dataset, for free:

```bash
jev-guard --backend local eval --out eval-local.html
```

!!! important "Thresholds are calibrated per backend"
    A policy's thresholds encode how confident *that backend* is. Measured on the golden
    set, the local zero-shot model picks "malicious" for the right messages but with about
    0.5 confidence, so `general`'s 0.8 threshold never fires and intent recall is 0. The
    same policy on Jev is unaffected. [`policies/general_local.yaml`](https://github.com/rudra72r/jev-guard/blob/main/policies/general_local.yaml)
    is `general` retuned for local models; re-measure with `jev-guard --backend local eval`
    whenever you switch backend or model.

## Jev without a TypeSafe account

TypeSafe's signups have been closed at times since launch. Cloudflare Workers AI serves the
same model with the same request and response shapes:

```bash
export CLOUDFLARE_ACCOUNT_ID=...   # dash.cloudflare.com, right-hand sidebar
export CLOUDFLARE_API_TOKEN=...    # a token with Account > Workers AI > Read
export JEV_GUARD_BACKEND=cloudflare
jev-guard eval                     # same policies, same verdicts
```

Usage is billed by Cloudflare; pass `price_per_million=` to `CloudflareJevBackend` if you
want cost figures to match your plan. Jev is also on **Vercel AI Gateway**
(`typesafe-ai/jev`, via the AI SDK) and **Netlify AI Gateway** (inside Netlify Functions),
both TypeScript-only today.

## An LLM as the judge

```bash
export JEV_GUARD_BACKEND=ollama:llama3.1          # a local LLM, no API key
export JEV_GUARD_BACKEND=openai:gpt-5.6-luna      # OPENAI_API_KEY
export JEV_GUARD_BACKEND=anthropic:claude-haiku-4-5
```

The model is asked the policy's questions and must answer with one JSON object of
probabilities. Percentages and stray prose are handled; anything unparseable is reported as
"no answer" in the verdict instead of being guessed.

!!! warning "A judge can be talked into things"
    An LLM judge reads the text it judges, so a crafted payload can try to steer it. The
    prompt treats the content strictly as untrusted data, which helps but can't make a
    text-generating model immune. Jev doesn't generate text, which is one reason it's the
    default.

## Surviving an outage

```bash
export JEV_GUARD_BACKEND=jev,local
```

Each backend is tried in turn. jev-guard falls back when one is unreachable, overloaded,
rate-limited, or has no key configured (so this chain also works offline with no key at
all). Each fallback is logged, and the verdict's model, which is `jev.model` in
[telemetry](telemetry.md), records which backend actually answered.

## Custom backends

Anything with these three methods works:

```python
class MyBackend:
    name = "mine"
    needs_typesafe_key = False
    input_price_per_million = 0.0

    def evaluate(self, state: dict, questions: dict) -> JevResult: ...
    async def aevaluate(self, state: dict, questions: dict) -> JevResult: ...
    def close(self) -> None: ...


backends.set_backend(MyBackend())
```

Return a `JevResult` with a `JevAnswer` per question you can answer (leave out the rest; the
verdict reports them as unanswered). Set `cost_usd` if you aren't priced like Jev.
