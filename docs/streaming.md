# Streaming

Checking a streamed answer only once it has finished is too late: the user has already read it. jev-guard checks while the stream is running, with one of two strategies set in the policy.

```python
async for token in guard.astream_check(llm_stream, user_message):
    if isinstance(token, StreamCut):
        show_notice(token)  # the stream was stopped; token.verdict says why
        break
    send_to_user(token)
```

`llm_stream` can yield strings or raw chunks from OpenAI (Chat Completions or Responses), Anthropic, or LangChain. Sync iterables work too.

## Buffer and check (default)

Chunks are held back until the text so far has been checked, then released. Nothing unchecked reaches the user.

- A check runs every `stream_check_every` chunks (default 40) and once more at the end.
- Each check adds about 70–500 ms before its batch is released, so time-to-first-token grows by one check.
- If a check blocks, the unchecked batch is never shown. The stream ends with a `StreamCut` whose text is the policy's safe reply (`retract` is `False`).

## Rollback (opt-in)

```yaml
stream_strategy: rollback
```

Chunks go straight through and checks run in the background, so time-to-first-token is unchanged. If a check blocks, the stream stops and a `StreamCut` with `retract=True` tells your app to remove the message it already showed (render "message removed by safety filter"). Use it when latency matters more than the user briefly seeing text that gets retracted.

## What each check sees

Every check covers the **whole response so far**, not just the newest chunks. An instruction split across chunks ("ignore all prev" + "ious instructions") is still seen whole. The tradeoff is cost growing faster than length:

| streamed chunks | every | checks | cost (general policy, estimated) |
|---|---|---|---|
| 300 | 40 | 8 | $0.00014 |
| 1,000 | 40 | 25 | $0.00089 |
| 1,000 | 100 | 10 | $0.00037 |
| 3,000 | 40 | 75 | $0.0066 |

For long outputs, raise `stream_check_every`.

## `StreamCut`

The last item of a stopped stream. It's a `str`, so an app that just joins tokens still shows a readable notice. It also carries:

- `verdict`: the blocking `Verdict`, with reasons
- `retract`: `True` in rollback mode (remove what was shown), `False` in buffer mode (what was shown was checked)

When a stream is stopped, jev-guard also closes the upstream stream, so you stop paying for tokens.

## With the client wrappers

`wrap_openai` and `wrap_anthropic` guard `create(..., stream=True)` and keep the SDK's own chunk objects. A block raises `GuardBlockedError` mid-iteration instead of yielding a marker:

```python
client = wrap_openai(AsyncOpenAI(), policy="general")
stream = await client.chat.completions.create(model=..., messages=..., stream=True)
try:
    async for chunk in stream:
        ...
except GuardBlockedError as blocked:
    show(blocked.suggested_response)
```

Full example: [`examples/04_streaming_openai.py`](https://github.com/rudra72r/jev-guard/blob/main/examples/04_streaming_openai.py).
