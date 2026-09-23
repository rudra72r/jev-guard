"""Check the input *while* the LLM generates, so the input check adds no latency.

The usual order (check, then call the LLM) puts the whole input check (~70-500 ms) in front
of every response. ``run_with_guard`` starts both at once:

    from jev_guard.parallel import run_with_guard

    result = await run_with_guard(guard, user_message, lambda: call_llm(user_message))
    reply = result.reply        # the LLM's answer, or the policy's safe reply if blocked

- If the input check blocks, the LLM call is cancelled (async) and the safe reply is
  returned. The LLM never answers the user.
- Otherwise the reply is output-checked as usual before it's returned.

Tradeoff: you pay for LLM calls on inputs that end up blocked (cancelled early when the
provider supports it), and an LLM with tools could act before the input check finishes.
Only use this for generation without side effects; for agents, check first or use
``jev_guard.agents.ToolGuard`` on every tool call.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from jev_guard.guard import Guard
from jev_guard.guards.output_guard import Context
from jev_guard.types import Verdict

__all__ = ["GuardedResult", "run_with_guard", "run_with_guard_sync"]

FALLBACK_REPLY = "Sorry, I can't help with that."


@dataclass(frozen=True, slots=True)
class GuardedResult:
    """What to show the user, plus both verdicts (``output`` is None if the input blocked)."""

    reply: str
    input: Verdict
    output: Verdict | None

    @property
    def blocked(self) -> bool:
        return self.input.blocked or bool(self.output and self.output.blocked)


def _safe_reply(verdict: Verdict) -> str:
    return verdict.suggested_response or FALLBACK_REPLY


async def run_with_guard(
    guard: Guard,
    user_message: str,
    generate: Callable[[], Awaitable[str]],
    *,
    context: Context = None,
) -> GuardedResult:
    """Run ``generate()`` and the input check concurrently; see the module docstring."""
    generation = asyncio.ensure_future(generate())
    try:
        v_in = await guard.acheck_input(user_message)
    except BaseException:
        generation.cancel()
        raise
    if v_in.blocked:
        generation.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await generation  # let it finish cancelling; its result or error is discarded
        return GuardedResult(_safe_reply(v_in), v_in, None)

    reply = await generation
    v_out = await guard.acheck_output(user_message, reply, context=context)
    return GuardedResult(_safe_reply(v_out) if v_out.blocked else reply, v_in, v_out)


def run_with_guard_sync(
    guard: Guard,
    user_message: str,
    generate: Callable[[], str],
    *,
    context: Context = None,
) -> GuardedResult:
    """Sync version using a thread. A blocked input can't stop a thread mid-call, so the
    LLM call runs to completion in the background and its answer is thrown away."""
    # Not a `with` block: exiting one waits for the thread, which would make a blocked input
    # wait for the whole LLM call anyway.
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jev-guard-generate")
    try:
        generation = pool.submit(generate)
        v_in = guard.check_input(user_message)
        if v_in.blocked:
            generation.cancel()  # only helps if it hasn't started yet
            return GuardedResult(_safe_reply(v_in), v_in, None)
        reply = generation.result()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    v_out = guard.check_output(user_message, reply, context=context)
    return GuardedResult(_safe_reply(v_out) if v_out.blocked else reply, v_in, v_out)
