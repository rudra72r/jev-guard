"""Pieces shared by the SDK integrations: the ``@guarded`` decorator and message helpers.

Nothing here imports openai or anthropic. The wrappers are duck-typed against the SDKs'
public shapes, so jev-guard never forces either SDK on you.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any, ParamSpec, TypeVar, cast, overload

from jev_guard.errors import GuardBlockedError
from jev_guard.guard import Guard
from jev_guard.policies.base import Policy
from jev_guard.types import Verdict

logger = logging.getLogger("jev_guard")

P = ParamSpec("P")
R = TypeVar("R")

FALLBACK_REPLY = "Sorry, I can't help with that."


def _prompt_from_call(
    func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> str:
    """The text to check: a ``prompt`` argument if there is one, else the first str argument."""
    bound = inspect.signature(func).bind_partial(*args, **kwargs)
    if isinstance(bound.arguments.get("prompt"), str):
        return cast(str, bound.arguments["prompt"])
    for value in bound.arguments.values():
        if isinstance(value, str):
            return value
    raise TypeError(
        f"@guarded couldn't find the prompt for {func.__qualname__}: give it a `prompt: str` "
        "parameter or pass the prompt as its first string argument"
    )


def _reply_for(verdict: Verdict) -> str:
    return verdict.suggested_response or FALLBACK_REPLY


def _log_review(verdict: Verdict) -> None:
    if verdict.action == "review":
        logger.warning("jev-guard: %s flagged for review: %s", verdict.stage, verdict.reasons)


@overload
def guarded(func: Callable[P, R], /) -> Callable[P, R]: ...


@overload
def guarded(
    *, policy: str | Policy | None = None
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def guarded(
    func: Callable[P, R] | None = None, /, *, policy: str | Policy | None = None
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """Guard a function that takes a prompt and returns the LLM's reply as a string.

    Before the call, the prompt is checked; if it's blocked, your function isn't called and the
    policy's safe reply is returned instead. After the call, the reply is checked the same way.
    ``review`` verdicts pass through and are logged as warnings. Works on sync and async
    functions. The prompt is a ``prompt`` argument, or else the first string argument.

    Usage: ``@guarded`` or ``@guarded(policy="writing_app")``.
    """

    def decorate(fn: Callable[P, R]) -> Callable[P, R]:
        guard = Guard(policy=policy)

        if inspect.iscoroutinefunction(fn):
            async_fn = cast(Callable[..., Awaitable[Any]], fn)

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                prompt = _prompt_from_call(fn, args, kwargs)
                v_in = await guard.acheck_input(prompt)
                if v_in.blocked:
                    return _reply_for(v_in)
                _log_review(v_in)
                reply = await async_fn(*args, **kwargs)
                if not isinstance(reply, str):
                    return reply
                v_out = await guard.acheck_output(prompt, reply)
                if v_out.blocked:
                    return _reply_for(v_out)
                _log_review(v_out)
                return reply

            async_wrapper.guard = guard  # type: ignore[attr-defined]
            return cast(Callable[P, R], async_wrapper)

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            prompt = _prompt_from_call(fn, args, kwargs)
            v_in = guard.check_input(prompt)
            if v_in.blocked:
                return cast(R, _reply_for(v_in))
            _log_review(v_in)
            reply = fn(*args, **kwargs)
            if not isinstance(reply, str):
                return reply
            v_out = guard.check_output(prompt, reply)
            if v_out.blocked:
                return cast(R, _reply_for(v_out))
            _log_review(v_out)
            return reply

        wrapper.guard = guard  # type: ignore[attr-defined]
        return wrapper

    if func is not None:
        return decorate(func)
    return decorate


# --- helpers for client wrappers ----------------------------------------------------------


def text_of(content: Any) -> str:
    """Plain text from a message's content: a string, or a list of typed parts / blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, Iterable):
        parts: list[str] = []
        for part in content:
            kind = _get(part, "type")
            text = _get(part, "text")
            if kind in (None, "text", "input_text", "output_text") and isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    return ""


def last_user_text(messages: Iterable[Any]) -> str:
    for message in reversed(list(messages)):
        if _get(message, "role") == "user":
            return text_of(_get(message, "content"))
    return ""


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def enforce(verdict: Verdict) -> Verdict:
    """Raise GuardBlockedError on block, log reviews, return the verdict otherwise."""
    if verdict.blocked:
        raise GuardBlockedError(verdict)
    _log_review(verdict)
    return verdict


def warn_stream_unchecked() -> None:
    logger.warning(
        "jev-guard: stream=True responses are not output-checked by the client wrapper; "
        "use Guard.astream_check for streamed output (the input was still checked)"
    )


_WRAPPED = "__jev_guard_wrapped__"


def patch_create(
    resource: Any,
    guard: Guard,
    prompt_of: Callable[[dict[str, Any]], str],
    reply_of: Callable[[Any], str],
) -> None:
    """Replace `resource.create` with a checked version (sync or async). Idempotent."""
    original = resource.create
    if getattr(original, _WRAPPED, False):
        return

    if inspect.iscoroutinefunction(original):

        @functools.wraps(original)
        async def acreate(*args: Any, **kwargs: Any) -> Any:
            prompt = prompt_of(kwargs)
            enforce(await guard.acheck_input(prompt))
            response = await original(*args, **kwargs)
            if kwargs.get("stream"):
                warn_stream_unchecked()
                return response
            enforce(await guard.acheck_output(prompt, reply_of(response)))
            return response

        setattr(acreate, _WRAPPED, True)
        resource.create = acreate
        return

    @functools.wraps(original)
    def create(*args: Any, **kwargs: Any) -> Any:
        prompt = prompt_of(kwargs)
        enforce(guard.check_input(prompt))
        response = original(*args, **kwargs)
        if kwargs.get("stream"):
            warn_stream_unchecked()
            return response
        enforce(guard.check_output(prompt, reply_of(response)))
        return response

    setattr(create, _WRAPPED, True)
    resource.create = create
