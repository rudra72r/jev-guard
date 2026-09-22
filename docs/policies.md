# Policies

A policy is a set of questions jev-guard asks Jev about each input and output, plus the rules that turn the answers into `allow`, `review`, or `block`. The five builtins cover common apps. Each is also a YAML file you can copy (`jev-guard policy show NAME`).

| Policy | For | Blocks on | Reviews on |
|---|---|---|---|
| `general` | any chat app (the default) | prompt injection > 0.85, malicious intent > 0.8 | PII, answers that miss the request |
| `writing_app` | writing and creative tools | same as general | PII |
| `support_agent` | customer support | legal/medical advice > 0.6, SLA promises > 0.7 | frustration ≥ 2 of 3, escalation requests, refund promises |
| `coding_agent` | coding agents | destructive commands, secrets > 0.75; any two "high" signals | network writes, privilege escalation, unsafe deserialization, SQL injection |
| `rag` | answers from retrieved docs | two or more grounding failures | an ungrounded answer, likely hallucination, contradiction |

Each builtin's docstring explains its tradeoffs and how to tune it.

## Questions

Each question has a type, the Jev primitive it uses:

- **noul**: a yes/no statement; Jev returns the probability it's true (0–1).
- **choice**: pick one label; Jev returns the label, its confidence, and every label's probability.
- **score**: rate on an ordered rubric; Jev returns a score between levels (e.g. 2.4 on 0–3).

And a rule for when it *fires*:

```yaml
contains_legal_or_medical_advice:
  type: noul
  instructions: The assistant_response gives legal advice or medical advice ...
  severity: critical
  threshold: 0.6           # fires when the probability > 0.6
frustration_level:
  type: score
  criteria: [Calm, Mildly annoyed, Clearly frustrated, Very angry]
  severity: high
  threshold: 2
  comparator: ">="         # fires when the score >= 2
intent:
  type: choice
  criteria: {benign: ..., borderline: ..., malicious: ...}
  flag: [malicious]        # fires when Jev picks a flagged label...
  threshold: 0.8           # ...with confidence > 0.8
```

Comparisons are strict by default: a value exactly at the threshold doesn't fire. For questions where a *low* value is the problem ("the answer is grounded in the context"), set `comparator: "<"` and `risk_when: low`.

Instructions refer to the fields Jev sees: `user_message`, `assistant_response`, and `context` (the documents passed to `check_output`).

## From answers to an action

Firing every question that crosses its threshold straight to "block" gives too many false positives once a policy has five or more questions. Instead, each question has a **severity** and a **weight** (defaults: critical 3, high 2, medium 1, low 0.5):

1. Any fired **critical** question blocks on its own.
2. Fired **high** questions add their weight to a sum. If the sum is over `block_threshold`, the check blocks; if it's over `review_threshold`, it goes to review. With the defaults (weight 2, `review_threshold: 1.0`), one high signal means review. In `coding_agent` and `rag` (`block_threshold: 3.0`), two together block.
3. Fired **medium** and **low** questions never change the action. They're listed in `reasons` and lower `confidence`.
4. `confidence = 1 − Σ(weight × risk) / Σ(weight)` over every answered question. Risk is 0–1: a noul's probability, the probability of a choice's flagged labels, or a score's position toward the risky end of its rubric. Totally clean text gets 1.0; totally suspicious text gets 0.0.

Every reason names the question, its value, the comparison, the threshold, and the severity. Nothing is a black box.

### Worked example

`support_agent`, input stage. A customer writes: *"This is the third time my order is late. I want to talk to a manager."* Suppose Jev answers:

| question | severity (weight) | Jev's answer | threshold | fires? | risk |
|---|---|---|---|---|---|
| `is_prompt_injection` | critical (3) | 0.03 | > 0.85 | no | 0.03 |
| `contains_pii` | high (2) | 0.10 | > 0.6 | no | 0.10 |
| `intent` | critical (3) | benign @ 0.95 (malicious 0.01) | malicious > 0.8 | no | 0.01 |
| `frustration_level` | high (2) | 2.4 of 3 | ≥ 2 | **yes** | 2.4 / 3 = 0.80 |
| `should_escalate_to_human` | high (2) | 0.82 | > 0.5 | **yes** | 0.82 |

- No critical question fired, so there's no automatic block.
- Two high questions fired: sum = 2 + 2 = **4.0**. That's over `review_threshold` 1.0. `support_agent` has no `block_threshold`, so the action is **review**. The frustrated customer goes to a human rather than being refused.
- Confidence = 1 − (3×0.03 + 2×0.10 + 3×0.01 + 2×0.80 + 2×0.82) / (3+2+3+2+2) = 1 − 3.56 / 12 = **0.70**.

The verdict's reasons:

```
frustration_level: 2.40 >= 2.00 (high)
should_escalate_to_human: 0.82 > 0.50 (high)
high-severity sum 4.00 > review 1.00
```

(These numbers come from running the real aggregation code.)

## Tuning

Change thresholds in code:

```python
from jev_guard import Guard, Policy

policy = Policy.from_builtin("general").override(thresholds={"is_prompt_injection": 0.7})
guard = Guard(policy=policy)
```

Or in YAML, starting from a builtin and changing only what you need:

```yaml
name: my_support
extends: support_agent
block_threshold: 3.0            # two high signals now block
input:
  is_prompt_injection:
    threshold: 0.7              # merged into the builtin question
  frustration_level: null       # removes an inherited question
output:
  mentions_competitor:          # adds a new question
    type: noul
    instructions: The assistant_response recommends a competitor's product.
    severity: medium
    threshold: 0.6
```

```python
guard = Guard(policy="./my_support.yaml")
```

```bash
jev-guard policy validate my_support.yaml
# my_support.yaml line 12: unknown question type 'nol' — did you mean 'noul'?
```

A good loop: lower thresholds until `jev-guard eval` or a labelled `jev-guard scan` shows the recall you need, then check the false alarms in the report's mistakes table.

## Streaming settings

Two policy fields control [streaming](streaming.md): `stream_strategy` (`buffer` or `rollback`) and `stream_check_every` (chunks between checks, default 40).

## Custom Python policies

Subclass `Policy` to register a named builtin, or override `aggregate()` for different decision logic. See [Custom policies](recipes/custom-policies.md).
