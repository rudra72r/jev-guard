# Custom policies

## Start from a builtin (YAML)

Most custom policies are a builtin with a few changes:

```yaml
# policies/edtech.yaml
name: edtech_tutor
version: 1.0.0
extends: general
description: Homework help for students aged 13-18.
input:
  asks_for_graded_answer:
    type: noul
    instructions: >-
      The user_message asks for the final answer to what looks like a graded homework or
      exam question, rather than help understanding it.
    severity: medium
    threshold: 0.7
output:
  age_inappropriate:
    type: noul
    instructions: The assistant_response contains content inappropriate for a 13-year-old.
    severity: critical
    threshold: 0.6
```

```bash
jev-guard policy validate policies/edtech.yaml
jev-guard check "Just give me the answer to question 4" --policy policies/edtech.yaml
```

Writing good questions:

- **Phrase nouls as statements about a named field.** "The user_message asks for …" reads better to Jev than a question, because Jev scores how true the statement is and follows instructions literally.
- **Avoid negations.** "The reply is not polite" is weaker than asking whether it's rude.
- **Give scores a rubric with a real description for every level**, lowest first.
- **Keep one idea per question.** Two concerns in one question make its probability hard to interpret.

## Every field

| field | default | meaning |
|---|---|---|
| `name`, `version`, `description` | required, `1.0.0`, `""` | reported in every verdict and span |
| `extends` | | start from a builtin and merge per question; `null` removes one |
| `review_threshold` | `1.0` | high-severity weight sum above which a check goes to review |
| `block_threshold` | `null` | high-severity weight sum above which a check blocks (`null`: only criticals block) |
| `suggested_responses` | `{}` | question name → safe reply when that question causes a block |
| `default_suggested_response` | `"Sorry, I can't help with that."` | safe reply otherwise |
| `context_fallback` | `null` | builtin whose output questions are used when no `context` is passed |
| `stream_strategy`, `stream_check_every` | `buffer`, `40` | see [Streaming](../streaming.md) |
| `input`, `output` | `{}` | question name → question |

Question fields: `type` (noul / choice / score), `instructions`, `criteria`, `severity` (low / medium / high / critical), `weight` (defaults to 0.5 / 1 / 2 / 3 by severity), `threshold`, `comparator` (`>`, `>=`, `<`, `<=`), `flag` (choice labels that count as risky), `risk_when` (`high` or `low`).

## In Python

Subclass `Policy` to ship a policy with your code. A subclass with a `name` default registers itself, so `Guard(policy="edtech_tutor")` works:

```python
from pydantic import Field

from jev_guard import Policy
from jev_guard.policies.general import INPUT_PII, INTENT, PROMPT_INJECTION
from jev_guard.types import QuestionSpec


class EdtechTutor(Policy):
    """Homework help for students aged 13-18."""

    name: str = "edtech_tutor"
    input: dict[str, QuestionSpec] = Field(
        default_factory=lambda: {
            "is_prompt_injection": PROMPT_INJECTION,
            "contains_pii": INPUT_PII,
            "intent": INTENT,
        }
    )
```

Override `aggregate(stage, answers)` to change how answers become an action. Return an `Aggregation(action, confidence, reasons, fired)`.

## Measure it

Put labelled examples in a JSONL file (`{"id": ..., "input": ..., "label": {"asks_for_graded_answer": true}}`) and run:

```bash
jev-guard eval --dataset my_cases.jsonl --policy policies/edtech.yaml
```
