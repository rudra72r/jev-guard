"""Example 04: guard a streamed OpenAI response.

    pip install "jev-guard[openai]"
    export TYPESAFE_API_KEY=sk-...    # https://console.typesafe.ai/keys
    export OPENAI_API_KEY=sk-...
    python examples/04_streaming_openai.py

Two ways to stream safely:

1. `guard.astream_check(stream, prompt)` yields text and, if it has to stop, a final
   `StreamCut` marker. With the default "buffer" strategy nothing unchecked reaches the user;
   each check (every 40 chunks, ~70-500 ms) delays the text a little.
2. `wrap_openai(client)` keeps OpenAI's own chunk objects and raises `GuardBlockedError`.

A 1,000-token answer costs about 25 checks, roughly $0.001 at Jev's current price.
"""

from __future__ import annotations

import asyncio
import os

from openai import AsyncOpenAI

from jev_guard import Guard, GuardBlockedError, Policy, StreamCut
from jev_guard.integrations.openai_sdk import wrap_openai

MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
PROMPT = "Explain in two short paragraphs how HTTPS keeps a login form safe."


async def with_astream_check() -> None:
    client = AsyncOpenAI()
    guard = Guard(policy="general")
    if guard.check_input(PROMPT).blocked:
        return

    stream = await client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": PROMPT}], stream=True
    )
    async for token in guard.astream_check(stream, PROMPT):
        if isinstance(token, StreamCut):
            # rollback mode sets token.retract: replace the whole message in your UI
            print(f"\n[stopped: {'; '.join(token.verdict.reasons)}]")
            print(token)
            return
        print(token, end="", flush=True)
    print()


async def with_rollback_strategy() -> None:
    """Rollback keeps time-to-first-token: text streams at once, checks run behind it."""
    policy = Policy.from_builtin("general").model_copy(update={"stream_strategy": "rollback"})
    guard = Guard(policy=policy)
    client = AsyncOpenAI()
    stream = await client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": PROMPT}], stream=True
    )
    shown: list[str] = []
    async for token in guard.astream_check(stream, PROMPT):
        if isinstance(token, StreamCut) and token.retract:
            print("\n[message removed by safety filter]")
            return
        shown.append(token)
        print(token, end="", flush=True)
    print()


async def with_wrapped_client() -> None:
    client = wrap_openai(AsyncOpenAI(), policy="general")
    stream = await client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": PROMPT}], stream=True
    )
    try:
        async for chunk in stream:  # OpenAI's own ChatCompletionChunk objects
            if chunk.choices and chunk.choices[0].delta.content:
                print(chunk.choices[0].delta.content, end="", flush=True)
    except GuardBlockedError as blocked:
        print(f"\n{blocked.suggested_response or '[stopped by safety filter]'}")
    print()


if __name__ == "__main__":
    for demo in (with_astream_check, with_rollback_strategy, with_wrapped_client):
        print(f"== {demo.__name__} ==")
        asyncio.run(demo())
