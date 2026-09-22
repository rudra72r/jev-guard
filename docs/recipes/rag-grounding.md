# RAG grounding checks

The `rag` policy's output questions only make sense next to the documents the answer was supposed to come from, so pass them as `context`:

```python
from jev_guard import Guard

guard = Guard(policy="rag")

docs = retriever.get(query)  # list[str]
answer = llm(query, docs)
verdict = guard.check_output(query, answer, context=docs)
```

It asks:

| question | severity | fires when |
|---|---|---|
| `answer_grounded_in_context` | high | probability < 0.5 that every claim is supported |
| `hallucination_risk` | high | score ≥ 2 of 3 (specific details not in the docs) |
| `answer_contradicts_context` | high | probability > 0.6 |
| `context_is_sufficient` | medium | probability < 0.5 (a retrieval problem, not a safety one) |
| `contains_citation` | low | probability < 0.5 |

One failure means review; two block (`block_threshold: 3.0`). If you forget `context`, jev-guard logs a warning and runs the `general` output checks instead, noting that in `verdict.reasons`.

## With LangChain

The callback handler picks up documents from any retriever in the same run, so there's nothing to wire:

```python
from jev_guard import GuardBlockedError
from jev_guard.integrations.langchain_cb import JevGuardCallbackHandler

handler = JevGuardCallbackHandler(policy="rag")  # one handler per request
try:
    answer = chain.invoke(question, config={"callbacks": [handler]})
except GuardBlockedError as blocked:
    answer = blocked.suggested_response
```

`handler.verdicts` holds every verdict for audit. Full example: [`examples/03_langchain_rag.py`](https://github.com/rudra72r/jev-guard/blob/main/examples/03_langchain_rag.py).

## For regulated answers

A bank or a healthcare provider usually wants any single grounding failure to block, and every answer to cite:

```yaml
name: rag_strict
extends: rag
block_threshold: 1.5            # one high signal (weight 2) now blocks
output:
  contains_citation:
    severity: high
```
