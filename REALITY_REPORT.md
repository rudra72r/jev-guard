# Reality Report — Section 0

Checked 2026-09-22 against the TypeSafe docs (`docs.typesafe.ai`, including `/api`, `/models`, `/sdk/python/changelog`), PyPI JSON metadata, and the source of the `typesafe-sdk` 0.7.1 wheel.

## 1. PyPI package
- **`typesafe-sdk` 0.7.1**, released 2026-09-21 by TypeSafe AI <support@typesafe.ai>. Import it as `typesafe_sdk`.
  - Releases: 0.5.7 (first public, 09-11), 0.6.0 (09-15), 0.7.0 (09-18), 0.7.1 (09-21). That is about one release every 3 days, so pin `typesafe-sdk>=0.7.1,<0.8`.
  - Dependencies: `httpx2>=2.0.0` (legitimate: it's the pydantic org's HTTP client, maintained by Tom Christie), `pydantic>=2.12.0`, `tenacity>=9`, `typing-extensions`.
- **Do not use `typesafe-ai` 0.1.0.** It's a third-party redirect shim uploaded by Gerome Dexheimer on 09-17 that just re-exports `typesafe-sdk`. It isn't official, so depending on it adds supply-chain risk for no benefit.
- `typesafe` 0.9.1 is an unrelated package from 2010.

## 2. Client and method
- Sync client: `TypeSafeClient(api_key=None, model=None, retry=None, timeout=None, headers=None, transport=None, http_client=None, base_url=None)`.
- Async client: `AsyncTypeSafeClient` (same constructor).
- Method: **`client.system_one(state, questions, *, model=None, retry=None, timeout=None, extra_headers=None, extra_body=None, response_model=None)`**. It is not called `decide` or `evaluate`.
- HTTP endpoint: `POST https://api.typesafe.ai/v1/systemone`.
- Environment variables: `TYPESAFE_API_KEY`, `TYPESAFE_DEFAULT_MODEL`, `TYPESAFE_BASE_URL`, `TYPESAFE_LOG_LEVEL`.

## 3. Primitives: classes or dicts?
**Both are accepted.** The pydantic classes are `Noul`, `Choice` and `Score`. The TypedDict equivalents are `{"type": "noul"|"choice"|"score", "instructions": ..., "criteria": ...}` (`NoulModel`, `ChoiceModel`, `ScoreModel`).
- `Noul(instructions=..., criteria={"true": ..., "false": ...} | None)`
- `Choice(instructions=..., criteria={label: description})`, up to 255 labels
- `Score(instructions=..., criteria=[level0, level1, ...])`, an ordered list of 2–10 levels. This changed in 0.6.0; before that it was a dict keyed by integers.

## 4. Response fields
The response is a `SystemOneResponse` with `.model` (e.g. `"jev-1.13.0"`), `.usage.input_tokens` / `.usage.output_tokens` (both typed `int | None`), `.answers: dict[str, Answer]`, and typed views `.nouls`, `.choices`, `.scores`.

| Primitive | Fields |
|---|---|
| `NoulAnswer` | `noul` (float 0–1). **This is the only field; there is no `confidence` and no `probabilities`.** |
| `ChoiceAnswer` | `choice` (str), `confidence` (float), `probabilities` (dict[str, float]) |
| `ScoreAnswer` | `score` (float, can fall between levels), `confidence`, `probabilities` (dict[int, float]), `legend` (dict[int, str\|obj\|list]) |

All answer models are frozen pydantic models, so they serialize with `.model_dump()`.

## 5. Pricing and context window
- Model `jev-1.13.0`. The aliases `jev-latest` and `jev-preview` both currently resolve to it.
- Price: **$0.042 per 1M input tokens. Output tokens are free.**
- Context window: 64k tokens for state plus all questions combined, and 32k tokens for state plus the single longest question. Vercel's page lists "32k", which is the lower of these two limits.
- Latency: 70–500 ms.

## 6. Rate limits
- 250,000 tokens/s and 1,200 requests/min. Going over returns HTTP 429 with a `retry-after` (or `retry-after-ms`) header. HTTP 529 means the service is overloaded.
- **The docs don't give a separate free or new-account tier.** The number above is the only one published, and the docs say limits "adjust dynamically". We'll only know the real limit on a new key when your key first hits a 429.
- The SDK retries automatically with backoff through `RetryPolicy` (built on tenacity).

## 7. Async
**Yes.** `AsyncTypeSafeClient` provides `await client.system_one(...)`, `async with`, and `aclose()`.

## 8. Streaming
**No.** Neither the SDK source nor the API reference has any way to stream a Jev response. It returns one JSON response per request, which is expected for a classifier. This doesn't block Section 10: that section is about streaming the *upstream LLM's* output through the guard, and it still works.

## 9. What in SPEC.md is wrong
**Main one: a Noul answer has only one field, `noul`.** The spec assumes every primitive has three fields. Nouls have no `confidence`, so Section 6's `confidence` for Noul-heavy policies has to come from the `noul` probability itself, and the `JevAnswer` wrapper must allow `confidence` to be missing.

Other problems found, which I'll log in `DEVIATIONS.md` once you approve:
1. **Section 4:** `Verdict` is described as a "frozen dataclass" but must also support `.model_dump()`. `.model_dump()` is a pydantic method, not a dataclass one. Fix: make `Verdict` a frozen pydantic `BaseModel`. That also matches the SDK's own frozen answer models.
2. **Section 13:** the spec requires `pydantic>=2`, but the SDK needs `pydantic>=2.12`. Fix: pin `pydantic>=2.12`.
3. **Cost math (Sections 1 and 12):** "$0.0004/check" works out to about 9,500 input tokens per check at $0.042/M. A typical guard check (a short user message plus 3–8 short questions) is more like 300–1,500 tokens, or about $0.00001–$0.00006. That makes "10,000 checks/day ≈ $4/day" roughly 10–40× too high. Fix: calculate the figure in `cost.py` from actual `usage.input_tokens` and publish the measured number. The spec's number is an overestimate, so it's safe to leave as a cap but misleading as an estimate.
4. **Section 0 URL:** `openrouter.ai/typesafe/jev-1.13` returns a 404, so there's no OpenRouter listing at that URL. Vercel lists the model as `typesafe-ai/jev`.
5. **`TypeSafeClient(api_key=...)` validates the key on construction** (added in 0.7.1). So `Guard(...)` with no key fails immediately at construction, not at the first check. Fix: create the client lazily inside `Guard`, so importing the library and running `policy show` or `validate` work without a key.
6. **Streaming (Section 10)** is fine as written, but the README should say plainly that Jev itself doesn't stream.

Also worth knowing: the `langchain-typesafe` package already ships `AutoModeMiddleware`, which blocks risky tool calls before they run, and TypeSafe publishes a "Guardrails for LLMs" cookbook. Both overlap with `coding_agent` and the LangChain integration. That affects the Section 16 Q3 decision and the "how does this compare" FAQ.

---
**Stopped here, as Section 0 requires. Waiting for "go" before Section 1.**
