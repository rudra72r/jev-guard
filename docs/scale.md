# Running at scale

Four things matter once you're past a few requests per second: added latency, the provider's
rate limit, repeated work, and checks you don't need.

## Zero added latency on input checks

The obvious order (check the message, then call the LLM) puts the whole input check in front
of every response. Run them together instead:

```python
from jev_guard.parallel import run_with_guard

result = await run_with_guard(guard, user_message, lambda: call_llm(user_message))
reply = result.reply  # the LLM's answer, or the safe reply if it was blocked
```

The LLM starts immediately, and the check runs beside it. If the check blocks, the LLM call
is cancelled (often before it even starts) and the user gets the policy's safe reply. Input
checking then costs **no extra wall-clock time**.

The tradeoff: you pay for LLM calls that end up blocked. `run_with_guard_sync` does the same
with a thread, though a running thread can't be cancelled, so a blocked input returns
immediately while the LLM call finishes in the background and its answer is discarded.

!!! warning "Not for agents"
    Only use this where generation has no side effects. If the model can call tools, it
    could act before the input check lands. Check first, or guard every tool call with
    [`ToolGuard`](agents.md).

## Staying under the rate limit

TypeSafe allows 1,200 requests per minute. jev-guard spaces Jev calls client-side (default
1,100/min with a burst of 20) so a busy process queues instead of collecting 429s:

```bash
export JEV_GUARD_MAX_RPM=600     # several processes sharing one key: divide the limit
export JEV_GUARD_MAX_RPM=0       # off (the SDK still retries 429s)
```

The limit is per process. Beyond ~20 checks/second across your fleet, ask TypeSafe about
higher limits, use `jev,local` so overflow is answered locally, or cache more aggressively.

## Not paying twice for the same check

Identical checks (same text, same questions, same backend) are served from an in-memory LRU
cache: no call, no cost, no latency. It's on by default, holding 1,024 entries for an hour.

```bash
export JEV_GUARD_CACHE_SIZE=10000
export JEV_GUARD_CACHE_SIZE=0     # disable
```

The key is a hash, so the cache holds answers, never the checked text. A cached verdict
reports `latency_ms=0`, `estimated_cost_usd=0`, and a model name ending in `(cached)`.

## Checking only what matters

For agents, the cost is per tool call. Skip the ones that can't hurt you, and keep checking
what comes back:

```python
tools = ToolGuard(
    skip_calls=("Read", "Glob", "Grep"),  # read-only calls: safe to run unchecked
    skip_results=(),  # but their output can still carry injections
)
```

Patterns are `fnmatch` style, so `mcp__docs__*` covers a whole MCP server. Skipped checks
cost nothing and say so in `verdict.reasons`. The Claude Code hook takes the same lists:

```bash
jev-guard hook claude-code --skip-calls "Read,Glob,Grep"
```

## What a check costs in time

| backend | typical latency |
|---|---|
| cache hit | 0 ms |
| Jev | 70–500 ms |
| LLM judge | 0.3–3 s, depending on model and host |
| local models | ~6 s per check on a 4-thread CPU; much faster on a GPU |

For streamed responses, raise `stream_check_every` on long outputs; see
[Streaming](streaming.md).
