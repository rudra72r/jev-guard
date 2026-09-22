# Deviations from SPEC.md

Where SPEC.md turned out to conflict with reality or with a hard constraint, the code follows
reality and the change is logged here. Evidence is in `REALITY_REPORT.md`.

| # | Spec says | We do | Why |
|---|---|---|---|
| 1 | `Verdict` is a frozen dataclass with `.model_dump()` | Frozen pydantic `BaseModel` | `.model_dump()` is a pydantic method; dataclasses don't have it. Matches the SDK's own frozen answer models. |
| 2 | Runtime dep `pydantic>=2` | `pydantic>=2.12` | `typesafe-sdk` 0.7.1 requires it. |
| 3 | SDK package unnamed | `typesafe-sdk>=0.7.1,<0.8` | The official package. It had breaking changes in both 0.6 and 0.7, so we pin the minor version. `typesafe-ai` on PyPI is an unofficial third-party shim and is not used. |
| 4 | Every primitive has three fields incl. `confidence` | Noul answers have only `noul`; `JevAnswer.confidence` is optional | Real API shape. A noul's probability is used as its risk directly. |
| 5 | Flat "$0.0004 per check" | Cost is computed per call from `usage.input_tokens` × $0.042/1M | $0.0004 implies about 9.5k tokens per check; real guard checks are about 300–1,500 tokens. Falls back to a chars/4 estimate if usage isn't reported. |
| 6 | (implicit) SDK client built with `Guard(...)` | Built lazily on the first check | Since 0.7.1 the SDK validates the key in its constructor. Lazy creation means `Guard(...)`, `policy show` and `validate` work without a key. |
| 7 | Nightly GitHub Action runs the golden eval against real Jev (~$0.20/run) | Removed; the eval runs locally only, on demand | Owner requirement: no API key is ever stored in CI, so the open-source repo can never bill the maintainer. |
| 8 | Integration tests gated on `TYPESAFE_API_KEY` | Gated on `TYPESAFE_API_KEY` **and** `JEV_GUARD_ALLOW_LIVE=1`, plus a 200k-token budget per session | Having a key in your shell for other reasons shouldn't be enough to spend money. |
| 9 | Layout has no module for `Guard` | Added `src/jev_guard/guard.py` | `guards/input_guard.py` and `guards/output_guard.py` hold the per-stage request builders; the public class needed its own home. |
| 10 | Section 5 rules ("review if X > t") vs Section 6 ("medium/low don't drive action alone") | Single-question review rules are `high` severity with weight 2.0 and `review_threshold` 1.0 | So one fired `high` question means review and several can block (when a policy sets `block_threshold`). Rejected alternative: summing raw probabilities, because one strong signal gets diluted when a policy has 5+ questions. |
| 11 | (unspecified) Behaviour when Jev is down | Checks raise `JevAPIError` | Rejected fail-open (it silently disables safety) and fail-closed (a Jev outage takes the app down). The app decides. |
| 12 | Choice rule "`intent == malicious` AND its confidence > 0.8" | Implemented exactly; the choice's *risk* for `confidence` is the summed probability of its flagged labels | Keeps `confidence` informative when Jev picks `borderline` but still puts real weight on `malicious`. |
