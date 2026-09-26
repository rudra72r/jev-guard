# jev-guard

jev-guard checks what goes into and comes out of your LLM. Each check is one call to TypeSafe's [Jev](https://typesafe.ai) model, which answers typed questions with probabilities in 70–500 ms. A typical input or output check costs about $0.00002.

```bash
pip install "jev-guard[local]"
jev-guard try                    # no key, no config: checks four example messages
```

```python
from jev_guard import Guard

guard = Guard(policy="support_agent")

v = guard.check_input(user_message)  # before the LLM call
if v.blocked:
    return v.suggested_response

v = guard.check_output(user_message, reply)  # before the user sees the reply
```

Every check returns a `Verdict`:

| field | meaning |
|---|---|
| `action` | `"allow"`, `"review"`, or `"block"` |
| `blocked` | `action == "block"` |
| `reasons` | what fired, e.g. `is_prompt_injection: 0.92 > 0.85 (critical)` |
| `confidence` | 0–1, how clean the text looks overall (1.0 = nothing suspicious) |
| `suggested_response` | a safe reply to show when blocked |
| `raw_answers` | Jev's answer to every question |
| `latency_ms`, `input_tokens_used`, `estimated_cost_usd` | what the check took |
| `policy_name`, `policy_version`, `stage` | for logs and telemetry |

`Verdict.model_dump()` gives plain JSON for logs and audit trails.

## Where to start

- **Just want to see it work?** `jev-guard try`. No account, no key, no arguments.
- **Evaluating it this afternoon?** [Quickstart](quickstart.md) walks through a working setup in 15 minutes.
- **Tuning for your traffic?** [Policies](policies.md) explains how answers become actions, with a worked example.
- **Streaming responses?** [Streaming](streaming.md) covers the buffer and rollback strategies.
- **Compliance and audit?** See [Telemetry](telemetry.md), [PII redaction](redaction.md), and batch scanning with the [command line](cli.md).
- **Specific stack?** The recipes cover coding agents, RAG, Langfuse traces, custom policies, and LiteLLM.

!!! warning "Not a silver bullet"
    jev-guard judges content. It doesn't replace authentication, authorization, rate limiting, or human review for high-stakes actions. Jev's answers are probabilities, so thresholds are tradeoffs to tune on your own data. v0.1 is designed and tested for English only.
