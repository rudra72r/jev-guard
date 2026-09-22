# jev-guard — Build Specification

You are an autonomous engineer building `jev-guard`, an open-source Python (and later TypeScript) guardrail library for LLM applications, powered by TypeSafe AI's **Jev** model. Jev launched Sept 15, 2026 — the ecosystem is nearly empty this week, and the goal of this sprint is to ship a library good enough that real teams adopt it within 30 days.

You are working with Krish, a final-year CSE student and co-founder of a creative studio. He wants surgical work, plain-language explanations alongside the code, and specifics — no generic advice. When you make a judgment call, name the alternative you rejected and why in one sentence. Do not flatter. If something in this spec is wrong or outdated, say so and propose the fix before proceeding.

---

## Section 0 — Verify reality before writing anything

Jev is 7 days old at the time this spec is being written. API shapes may already have shifted. Before touching code:

1. `WebFetch` each of these and read them fully:
   - https://docs.typesafe.ai/introduction/quickstart
   - https://docs.typesafe.ai/concepts/system-one
   - https://docs.typesafe.ai/ (find the API reference and the limits page)
   - https://www.langchain.com/blog/building-a-harness-with-jev
   - https://dev.to/valyuai/how-to-use-jev-a-practical-guide-to-typesafes-system-one-model-g5e
   - https://openrouter.ai/typesafe/jev-1.13
   - https://vercel.com/ai-gateway/models/jev

2. Run `pip index versions typesafe_sdk` and `pip index versions typesafe-ai` to find the real package name.

3. Post a **Reality Report** to me containing exactly:
   - Actual PyPI package name and latest version
   - Client class name, method name (`decide` / `evaluate` / `systemone` / other)
   - Whether primitives are classes (`Choice`, `Score`, `Noul`) or dict literals `{"type": "choice", ...}`
   - The three response fields for each primitive (e.g. `choice`, `confidence`, `probabilities`)
   - Current per-model pricing and context window
   - Rate limits on a free/new account
   - Async support — yes/no, and how
   - Streaming — yes/no
   - One thing in this SPEC.md that is factually wrong given what you found, or "nothing wrong"

4. **Stop and wait for me to say "go"** before Section 1. Do not begin coding. If I have not replied within your session, do not proceed — write the report to `REALITY_REPORT.md` and end.

Anywhere below where this spec conflicts with what you found in Section 0, **reality wins** — adapt the code, note the deviation in `DEVIATIONS.md`, and keep moving.

---

## Section 1 — What we're building, in one paragraph

`jev-guard` is a drop-in guardrail layer that sits between any application and any LLM (OpenAI, Anthropic, local, whatever). It uses Jev's typed probabilistic outputs to classify **inputs** *before* the expensive LLM call (prompt injection, PII, malicious intent) and **outputs** *after* (hallucination shape, off-policy content, PII leak, unsafe actions), at roughly $0.0004 per check and 70–500 ms latency. The value proposition is: today, teams either use another LLM as a judge (10–100× more expensive, 5–20× slower) or brittle regex; `jev-guard` is the third option that didn't exist a week ago.

**Non-goals — put these in the README:**
- Not a replacement for authn/authz, WAF, or rate limiting
- Not deterministic — Jev is probabilistic, thresholds must be tuned
- English-first for v0.1; do not claim other-language support
- Not a policy engine for real-world laws (GDPR, HIPAA compliance requires human review)
- Not a jailbreak-detection silver bullet — layered defense, not a magic shield

---

## Section 2 — The audience and their pain

Write everything (README, error messages, CLI help) for these three people. If a design choice doesn't help at least one of them, drop it.

- **Priya, senior engineer at a 40-person AI startup.** Ships a customer-support agent on OpenAI. Just got asked by her CTO to "add guardrails before we go to enterprise." Has 2 hours to evaluate options this Friday. Will not read a 3000-word README. Cares about: install time, one working example she can copy, cost estimate, whether it breaks streaming.
- **Marcus, staff engineer at a Fortune 500 bank exploring internal LLM tools.** Needs an audit trail, PII redaction, and an offline batch scanner for compliance. Will pay for a supported version later. Cares about: OpenTelemetry, deterministic policy YAML files, self-hostable, MIT license.
- **Alex, indie hacker building a Claude-powered writing app.** Wants to stop users from prompt-injecting the system prompt. Has never heard of Jev. Cares about: a decorator he can slap on his function, a sensible default policy that just works.

The default policy must make Alex's app safer with zero configuration. The YAML loader must let Marcus customize without touching code. The one-page quickstart must let Priya evaluate in 15 minutes.

---

## Section 3 — Repo layout

```
jev-guard/
├── src/jev_guard/
│   ├── __init__.py                 # public re-exports: Guard, Policy, Verdict
│   ├── client.py                   # thin sync+async wrapper over TypeSafe SDK
│   ├── types.py                    # Verdict, Action, GuardStage, dataclasses
│   ├── errors.py                   # JevGuardError hierarchy
│   ├── policies/
│   │   ├── base.py                 # Policy abstract class + registry
│   │   ├── general.py
│   │   ├── support_agent.py
│   │   ├── coding_agent.py
│   │   ├── rag.py
│   │   ├── writing_app.py          # Alex's use case, minimal defaults
│   │   └── loader.py               # YAML → Policy
│   ├── guards/
│   │   ├── input_guard.py
│   │   ├── output_guard.py
│   │   └── streaming.py            # buffer-and-check for streamed LLM output
│   ├── integrations/
│   │   ├── openai_sdk.py           # @guarded decorator + monkey-patch helper
│   │   ├── anthropic_sdk.py
│   │   ├── langchain_cb.py         # BaseCallbackHandler subclass
│   │   └── litellm_proxy.py        # optional pre/post hooks
│   ├── redact.py                   # PII redaction utility (regex + Jev noul)
│   ├── telemetry.py                # OpenTelemetry spans (optional import)
│   ├── cost.py                     # token counting + $/check estimator
│   ├── cli.py                      # `jev-guard` entrypoint (Typer)
│   └── eval/
│       ├── golden.py               # runner
│       ├── report.py               # HTML + JSON reports (Jinja2)
│       └── datasets/
│           ├── jailbreaks.jsonl    # ~50 curated samples
│           ├── clean.jsonl         # ~30 benign
│           └── pii.jsonl           # ~20 with synthetic PII
├── tests/
│   ├── unit/                       # mock Jev, 80%+ coverage
│   ├── integration/                # gated on TYPESAFE_API_KEY env var
│   └── conftest.py
├── examples/
│   ├── 01_openai_chat_wrapped.py
│   ├── 02_anthropic_agent.py
│   ├── 03_langchain_rag.py
│   ├── 04_streaming_openai.py
│   └── 05_batch_scan.py
├── policies/                       # user-facing sample YAML
│   ├── strict.yaml
│   ├── permissive.yaml
│   └── coding_agent_prod.yaml
├── docs/                           # mkdocs-material
│   ├── index.md
│   ├── quickstart.md
│   ├── policies.md
│   ├── cost.md
│   ├── faq.md
│   └── recipes/
├── .github/
│   ├── workflows/ci.yml            # pytest, ruff, mypy, on 3.10/3.11/3.12
│   └── workflows/publish.yml       # PyPI on tag push (manual approval)
├── README.md
├── CHANGELOG.md
├── LICENSE                         # MIT
├── pyproject.toml                  # PEP 621, hatchling backend
├── .env.example
└── .gitignore
```

---

## Section 4 — The public API (freeze this contract)

The single most important thing about this library is that the public API is small, boring, and stable. Everything below is the v0.1 contract — do not add methods, do not add kwargs, do not "improve" it. Extras belong in a v0.2 discussion.

```python
from jev_guard import Guard, Policy, Verdict, Action

# 1. Construct from a named builtin policy (Alex's path)
guard = Guard(policy="writing_app")

# 2. Construct from a Policy object (Priya's path)
guard = Guard(policy=Policy.from_builtin("support_agent").override(
    thresholds={"is_prompt_injection": 0.9},
))

# 3. Construct from a YAML file (Marcus's path)
guard = Guard(policy="./policies/strict.yaml")

# --- Sync checks ---
v: Verdict = guard.check_input(user_message)
if v.blocked:
    return v.suggested_response or "Sorry, I can't help with that."

v: Verdict = guard.check_output(
    user_message,
    llm_response,
    context=retrieved_docs,   # optional, for RAG grounding checks
)

# --- Async checks (identical signatures) ---
v = await guard.acheck_input(user_message)
v = await guard.acheck_output(user_message, llm_response, context=None)

# --- Streaming (buffer-and-check strategy; documented tradeoff) ---
async for token in guard.astream_check(llm_stream, user_message):
    yield token   # jev-guard yields tokens until it decides to cut the stream

# --- Decorator (Alex's path) ---
from jev_guard.integrations.openai_sdk import guarded

@guarded(policy="writing_app")
def ask_llm(prompt: str) -> str:
    return openai_client.chat.completions.create(...).choices[0].message.content

# --- Context manager for grouped checks with shared telemetry ---
with Guard(policy="rag") as g:
    v_in = g.check_input(query)
    if v_in.blocked: return
    answer = call_llm(query)
    v_out = g.check_output(query, answer, context=docs)
```

**`Verdict` fields (frozen dataclass):**

| field | type | notes |
|---|---|---|
| `action` | `Literal["allow", "review", "block"]` | driven by policy thresholds |
| `blocked` | `bool` | convenience: `action == "block"` |
| `stage` | `Literal["input", "output"]` | which guard produced this |
| `confidence` | `float` | aggregate 0.0–1.0, defined per policy |
| `raw_answers` | `dict[str, JevAnswer]` | typed wrapper around Jev's response |
| `reasons` | `list[str]` | human-readable, e.g. "prompt_injection: 0.92 > 0.85" |
| `latency_ms` | `float` | wall-clock of the Jev call |
| `input_tokens_used` | `int` | for cost tracking |
| `estimated_cost_usd` | `float` | using current Jev pricing |
| `suggested_response` | `str \| None` | for blocked inputs, a safe reply |
| `policy_name` | `str` | for telemetry |
| `policy_version` | `str` | semver of the policy definition |

**Every Verdict must be JSON-serializable via `.model_dump()` — this is how telemetry and CLI reports consume it.**

---

## Section 5 — The policies (this is where the real value lives)

Each policy is a subclass of `Policy` and declares: `questions` (dict fed to Jev), `thresholds` (dict driving `action`), `aggregator` (function producing `confidence` and `reasons`), and optionally `suggested_response_for` (dict mapping reason → safe reply string). Every builtin policy also has a machine-readable YAML twin in `policies/` for users to copy.

### `general` — the default when the user says nothing
- **Input questions:** `is_prompt_injection` (Noul), `contains_pii` (Noul), `intent` (Choice: `benign` / `borderline` / `malicious`)
- **Output questions:** `contains_pii` (Noul), `is_off_topic` (Noul), `matches_user_intent` (Score 0–3 with legend)
- **Rules:** `block` if `is_prompt_injection > 0.85` OR (`intent == "malicious"` AND its confidence > 0.8); `review` if `contains_pii > 0.6` OR `matches_user_intent <= 1`; else `allow`.
- **Rationale to put in the docstring:** thresholds tuned for low false-positive rate — Alex's writing-app users hate being blocked, so we err toward `review` unless we're confident.

### `writing_app` — Alex's use case, sensible zero-config default
- Same as `general` but `matches_user_intent` is dropped (writing apps have loose intent) and `is_off_topic` threshold is loosened to 0.85.
- Optimized for: writer typing weird stuff, injection attempt, PII in a story about someone.

### `support_agent` — Priya's use case
- Adds: `contains_refund_promise` (Noul), `contains_legal_or_medical_advice` (Noul), `frustration_level` (Score 0–3), `should_escalate_to_human` (Noul), `contains_sla_commitment` (Noul).
- `block` any output where `contains_legal_or_medical_advice > 0.6` or `contains_sla_commitment > 0.7` — these create legal liability.
- `review` when `should_escalate > 0.5` or `frustration_level >= 2`.

### `coding_agent` — for Cursor / Claude Code / Cline-style tools
- **Input:** `contains_destructive_command` (Noul — `rm -rf`, `DROP TABLE`, `sudo shutdown`, `dd if=`), `touches_secrets_or_env` (Noul), `network_egress_intent` (Choice: `none` / `read` / `write`), `attempts_privilege_escalation` (Noul).
- **Output:** `suggests_destructive_command` (Noul), `hardcoded_secret_present` (Noul), `unsafe_deserialization` (Noul — pickle, yaml.load without SafeLoader), `sql_injection_risk` (Noul).
- `block` on any destructive/secret item > 0.75. This is the policy most likely to get adopted by dev-tool teams — invest in it.

### `rag` — Marcus's bank use case
- **Input:** `is_prompt_injection` (Noul), `contains_pii` (Noul), `intent` (Choice).
- **Output:** `answer_grounded_in_context` (Noul), `contains_citation` (Noul), `hallucination_risk` (Score 0–3), `context_is_sufficient` (Noul), `answer_contradicts_context` (Noul).
- **Special:** `check_output` MUST accept a `context` kwarg and include it in the Jev state — this is the whole point of the RAG policy. If context is None, log a warning and fall back to `general` behavior on the output.

**Every policy must:**
1. Have a class docstring explaining the target use case, the tradeoffs made, and one sentence on how to tune it.
2. Ship a matching `<name>.yaml` in `policies/`.
3. Have a test that verifies `Policy.from_builtin(name)` and `Policy.from_yaml(f"policies/{name}.yaml")` produce equivalent behavior on the golden dataset.

---

## Section 6 — Threshold aggregation and the "action" decision

This is subtle enough to get wrong. Read this section twice.

The naive approach — "if any question exceeds its threshold, block" — produces too many false positives when you have 5+ questions per policy. Instead:

- Each question in the policy has a **weight** and a **severity** (`low` / `medium` / `high` / `critical`).
- `critical` questions can single-handedly `block` (e.g. destructive command in coding_agent).
- `high` questions contribute to a weighted sum; if sum > `block_threshold` → block, if > `review_threshold` → review.
- `medium` and `low` questions influence the aggregate `confidence` field but don't drive `action` alone.
- The `confidence` field is `1.0 - normalized_weighted_sum`, so a totally clean input has confidence 1.0 and a totally suspicious one has 0.0.

Document this in `docs/policies.md` with a worked example. Every reason in `Verdict.reasons` must include which question fired, its value, its threshold, and its severity — no black boxes.

---

## Section 7 — The CLI (`jev-guard`)

Built with Typer. Commands:

```
jev-guard scan LOGS.jsonl [--policy NAME] [--out report.html] [--format html|json|md]
    Reads JSONL of {input, output, context?, [label]}. Also accepts
    Langfuse export shape (traces.jsonl) and Arize shape — detect by header.
    Outputs a report with: counts by action, top 20 worst offenders with
    reasons, total dollar cost, latency histogram, and — if `label` field
    is present — precision/recall/F1 per question.

jev-guard eval [--policy NAME] [--dataset PATH]
    Runs the golden dataset. Prints precision/recall/F1 per question and
    a total dollar figure. Non-zero exit if any question falls below its
    minimum score in the dataset manifest.

jev-guard policy list
jev-guard policy show NAME
jev-guard policy validate PATH.yaml
    YAML linter with helpful errors: "line 12: unknown question type
    'nol' — did you mean 'noul'?"

jev-guard check "user message here" [--policy NAME] [--output "llm response"]
    One-shot for shell scripts and quick manual testing.
```

Every command exits 0 on success, non-zero on user error, and prints a one-line "next step" hint on error. No stack traces to the user unless `--debug`.

---

## Section 8 — Telemetry (OpenTelemetry, optional)

`from jev_guard.telemetry import setup_telemetry; setup_telemetry()` — one line, and every `Guard` call emits a span named `jev_guard.check` with attributes:

```
policy.name
policy.version
guard.stage             # "input" | "output"
verdict.action
verdict.confidence
verdict.blocked
verdict.reasons         # comma-joined
latency.ms
tokens.input
cost.usd
jev.model               # e.g. "jev-1.13.0"
```

If OpenTelemetry isn't installed, `setup_telemetry()` prints a helpful "pip install jev-guard[otel]" and returns. Zero required config: spans only export if the user has already configured an exporter (Langfuse, Arize, Honeycomb, Jaeger, Sentry all just work).

**Test:** verify with `opentelemetry-sdk`'s `InMemorySpanExporter` that all expected attributes are present on both input and output spans.

---

## Section 9 — PII redaction (bonus utility, small surface)

```python
from jev_guard import redact
clean_text, found = redact(text, level="strict")
# found: list[{"type": "email"|"phone"|"ssn"|"credit_card"|"custom", "span": (start,end), "value": "***"}]
```

Two-pass: regex first for the well-known patterns, then a single Jev call with `contains_pii` on any chunk the regex didn't touch. Level `strict` uses Jev, level `fast` skips it. Document the accuracy tradeoff.

---

## Section 10 — Streaming (this is the hard one)

Naive streaming guardrails are useless — by the time the LLM has streamed a jailbroken instruction, it's too late. Two strategies to implement:

1. **Buffer-and-check** (default): buffer up to `stream_check_every` tokens (default 40), run `check_output` on the buffer, then release. Adds ~200 ms latency per check but works with any LLM. Document that first-token latency is not preserved.
2. **Post-stream-check-with-rollback-marker** (opt-in): stream through immediately, but on `block` verdict, yield a special sentinel token the wrapper app can use to render a "message removed by safety filter" UI. Preserves TTFT (time-to-first-token) but requires app cooperation.

Ship both. Default is buffer-and-check because it's safer for Alex's zero-config case.

---

## Section 11 — Testing strategy

- **Unit tests** mock the TypeSafe client with a `FakeJevClient` that returns programmable responses. Must cover: every policy's threshold logic, YAML loader edge cases, aggregation math (weighted sums, criticals, tie-breaking), Verdict serialization roundtrip, streaming buffer boundary conditions, redact regex + Jev fallback.
- **Integration tests** live in `tests/integration/`, are gated on `TYPESAFE_API_KEY`, are marked `@pytest.mark.integration`, and are skipped in CI by default. One test per policy: send a known-bad input, assert `block`; send a known-good input, assert `allow`.
- **Golden dataset eval** runs in a nightly GitHub Action against real Jev, reports precision/recall to a badge in the README. Nightly, not per-commit, because it costs real money — budget ~$0.20/run.
- Coverage target: 80%+ on `src/jev_guard/` (excluding `integrations/` files that need real SDKs).

---

## Section 12 — Documentation (README is 80% of the launch)

**README structure (in this exact order — do not reorder):**

1. One-sentence description
2. Badges: PyPI version, Python versions, CI status, coverage, license, "Powered by Jev" (link to typesafe.ai)
3. **30-second install** — `pip install jev-guard` + 4-line code block that actually blocks a jailbreak
4. **Why this exists** — the 3-option paragraph from Section 1
5. **Not a silver bullet** — the non-goals list. This paragraph is what earns trust with senior engineers; do not skip it.
6. **5-minute quickstart** — copy `examples/01_openai_chat_wrapped.py` inline
7. **Policies at a glance** — table of the 5 builtins with target use case and 3 example questions each
8. **Cost math** — worked example: "10,000 checks/day with support_agent policy ≈ $4/day"
9. **FAQ** — streaming? tuning thresholds? determinism? multiple languages? cost cap? how does this compare to Guardrails AI / NeMo Guardrails / LLM-Guard?
10. **Roadmap** — TypeScript port, more policies, hosted eval dashboard
11. **Credits** — cite Jev's Sept 15 2026 launch, Diogo Almeida, link Armin Ronacher's post about using Jev for agent monitoring
12. **License** — MIT

Also generate `docs/` as an mkdocs-material site with recipes: "Guarding a Claude Code agent", "RAG grounding checks", "Batch scanning Langfuse traces", "Custom policies", "Deploying behind LiteLLM proxy".

---

## Section 13 — Constraints and tooling

- **Python 3.10+** (`Literal`, `X | Y` unions, dataclass `slots`)
- **Runtime deps:** whatever the real TypeSafe SDK package is (from Section 0), `pydantic>=2`, `pyyaml`. Everything else in extras: `[otel]`, `[cli]` (typer + rich + jinja2), `[langchain]`, `[anthropic]`, `[openai]`, `[all]`
- **Dev deps:** pytest, pytest-asyncio, pytest-cov, ruff, mypy, hatch
- **`ruff` config:** enable E, F, I, N, UP, B, SIM, ARG, PL. Line length 100.
- **`mypy` strict** on `src/`, permissive on `tests/`
- **`pyproject.toml`** uses hatchling backend, PEP 621 metadata, entry point `jev-guard = jev_guard.cli:app`
- **`.env.example`** documents `TYPESAFE_API_KEY`, `JEV_GUARD_DEFAULT_POLICY`, `JEV_GUARD_TELEMETRY_ENABLED`
- **CI matrix:** Python 3.10, 3.11, 3.12 on ubuntu-latest. Publish workflow triggers on `v*` tag push, requires manual approval, uses PyPI trusted publishing.

---

## Section 14 — Phased build plan

**Commit at the end of every phase with a Conventional Commits message. Run `ruff check`, `mypy`, and `pytest` before every commit. If tests fail, stop and tell me — do not push through red.**

- **Phase 1 (~2h) — Skeleton + core**
  Scaffold, `pyproject.toml`, `Verdict`, `Policy` base, `general` policy, `client.py` wrapper, `check_input`, `check_output`. One integration test hitting real Jev.
  *Commit:* `feat: scaffold + general policy with input/output checks`

- **Phase 2 (~2h) — Policies + YAML**
  `support_agent`, `coding_agent`, `rag`, `writing_app`. YAML loader + validator. Aggregator with weights/severities. Tests for threshold math.
  *Commit:* `feat: add builtin policies and yaml loader`

- **Phase 3 (~2h) — Integrations**
  OpenAI + Anthropic decorators. `examples/01`, `02`. LangChain callback + `examples/03`.
  *Commit:* `feat: openai, anthropic, langchain integrations`

- **Phase 4 (~2h) — CLI**
  Typer app, `scan` with Jinja2 HTML report, `policy` subcommands, `check` one-shot. Detect Langfuse/Arize JSONL shapes.
  *Commit:* `feat: cli with scan/eval/policy commands`

- **Phase 5 (~2h) — Eval harness + golden dataset**
  Curate ~100 samples (50 jailbreaks from OWASP LLM Top 10 + garak + promptbench; 30 clean; 20 PII with synthetic values — never real). Eval runner, HTML/MD report, nightly CI workflow.
  *Commit:* `feat: golden eval dataset and harness`

- **Phase 6 (~2h) — Streaming + telemetry + redact**
  Buffer-and-check streaming, sentinel-token streaming, OpenTelemetry hook, `redact()` utility.
  *Commit:* `feat: streaming guards, otel telemetry, pii redact`

- **Phase 7 (~2h) — Docs + polish + release prep**
  README, mkdocs site, CHANGELOG, LICENSE, `.env.example`, launch-post drafts in `LAUNCH.md` (HN, X, r/LocalLLaMA, r/LangChain — Krish will edit these in his own voice). PyPI dry-run with `hatch build && twine check dist/*`. Tag `v0.1.0` locally, do NOT push.
  *Commit:* `chore: docs, changelog, release prep for 0.1.0`

**Total: ~14 hours across 2 focused days.** After Phase 7, stop and hand back to Krish for: PyPI publish, launch posts, social outreach.

---

## Section 15 — Ship checklist (must be true before you say "done")

- [ ] `pip install -e .` in a clean venv, `jev-guard --help` works
- [ ] All examples in `examples/` run end-to-end with a real `TYPESAFE_API_KEY`
- [ ] `pytest` green with real key; `pytest -m "not integration"` green without
- [ ] `ruff check .` clean; `mypy src/` clean
- [ ] Coverage badge shows ≥80%
- [ ] README renders correctly on GitHub (check code fences, tables, images)
- [ ] `hatch build` produces sdist + wheel; `twine check dist/*` passes
- [ ] `docs/` builds locally with `mkdocs serve`
- [ ] `LAUNCH.md` has drafts for HN "Show HN", X thread, Reddit posts
- [ ] Repo topics set: `llm`, `guardrails`, `jev`, `typesafe-ai`, `agents`, `prompt-injection`, `llm-security`, `openai`, `anthropic`, `langchain`
- [ ] `CHANGELOG.md` has a `0.1.0` entry with the "Added" list
- [ ] `LICENSE` is MIT with year 2026 and Krish's name (ask for the exact form to use)

---

## Section 16 — What to ask me before Phase 1

After the Reality Report is done and I say "go," ask these in one message:

1. GitHub username and the exact name to put in `LICENSE`
2. Confirm PyPI name `jev-guard` — reserve it now with a stub upload if you want, otherwise fall back to `jevguard`
3. Whether to skip Phase 3 LangChain integration (adds a heavy dep) or keep it
4. Whether OpenAI is `openai>=1.0` (new client) or the legacy 0.x — assume new unless I say otherwise
5. My `TYPESAFE_API_KEY` — I'll paste it, you put it in `.env` (which must be `.gitignore`d), never commit it, and never echo it back to me in full

---

## Section 17 — What to do if something goes wrong

- **Jev API differs from what this spec says** → Reality Report caught it; adapt, log in `DEVIATIONS.md`, keep moving.
- **Tests fail after a phase** → stop, post the failing test names and the last 20 lines of output, wait for me. Do not silently skip or `pytest.mark.xfail` your way past a red bar.
- **You hit a rate limit on Jev during integration tests** → back off, switch to mocked tests for that phase, note it in the phase summary.
- **You're not sure whether an API decision is right** → ask, don't guess. One well-timed question beats an hour of rework.
- **You realize this spec has a bug or a contradiction** → say so, propose the fix, wait for approval before proceeding. This spec is not sacred; reality wins.
- **You finish a phase and realize the next one is bigger than the estimate** → say so, propose a scope cut, do not silently blow the timeline.

---

**Now begin at Section 0. Post the Reality Report and stop. Do not proceed to Section 1 until I reply "go."**