# Cost

Jev charges **$0.042 per million input tokens**; output tokens are free ([TypeSafe models page](https://docs.typesafe.ai/models)). A check's input is the text being checked plus the policy's questions, so cost scales with message length and question count.

## Per check

Estimated for a typical support exchange (a two-sentence question, a four-sentence reply, about 4 characters per token):

| policy | input check | output check | per conversation | 10,000 conversations/day |
|---|---|---|---|---|
| `general` | ~260 tokens · $0.000011 | ~300 tokens · $0.000013 | $0.000024 | $0.24 |
| `writing_app` | ~260 · $0.000011 | ~220 · $0.000009 | $0.000020 | $0.20 |
| `support_agent` | ~400 · $0.000017 | ~470 · $0.000020 | $0.000037 | $0.37 |
| `coding_agent` | ~340 · $0.000014 | ~380 · $0.000016 | $0.000030 | $0.30 |
| `rag` (2 short docs) | ~260 · $0.000011 | ~420 · $0.000018 | $0.000028 | $0.28 |

These are estimates. Each `Verdict` reports the real `input_tokens_used` and `estimated_cost_usd` from Jev's usage data. To estimate your own traffic for free:

```bash
jev-guard scan your_logs.jsonl --policy support_agent --dry-run
```

## What makes it cost more

- **Long messages and documents.** RAG output checks include the retrieved context. Jev accepts up to 64k tokens per request (32k for the state plus the longest question).
- **Streaming.** Each check re-reads the answer so far, which is what catches instructions split across chunks. A 1,000-chunk reply costs about $0.001 at the default `stream_check_every: 40`, and 3,000 chunks cost about $0.0066. Raise the interval for long outputs. See [Streaming](streaming.md).
- **Strict redaction.** One extra call per `redact()`.

Empty text is never sent (the check is skipped and costs $0), and a policy with no questions for a stage makes no call for that stage.

## Rate limits

TypeSafe currently allows 250,000 tokens per second and 1,200 requests per minute, and may adjust this. The SDK retries `429` and `529` responses automatically. `jev-guard scan` runs 4 checks in parallel by default, which stays under the limit.
