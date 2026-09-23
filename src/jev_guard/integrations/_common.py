"""Pieces shared by the SDK integrations: the ``@guarded`` decorator and message helpers.

Nothing here imports openai or anthropic. The wrappers are duck-typed against the SDKs'
public shapes, so jev-guard never forces either SDK on you.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar, cast, overload

from jev_guard._fields import field
from jev_guard.errors import GuardBlockedError
from jev_guard.guard import Guard
from jev_guard.guards.streaming import achecked_stream, checked_stream

if TYPE_CHECKING:
    from jev_guard.agents import ToolGuard
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
            kind = field(part, "type")
            text = field(part, "text")
            if kind in (None, "text", "input_text", "output_text") and isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    return ""


def last_user_text(messages: Iterable[Any]) -> str:
    for message in reversed(list(messages)):
        if field(message, "role") == "user":
            return text_of(field(message, "content"))
    return ""


def enforce(verdict: Verdict) -> Verdict:
    """Raise GuardBlockedError on block, log reviews, return the verdict otherwise."""
    if verdict.blocked:
        raise GuardBlockedError(verdict)
    _log_review(verdict)
    return verdict


class CheckedStream:
    """A sync SDK stream whose chunks are output-checked. Other attributes pass through.

    Iterating yields the original chunk objects; a block raises ``GuardBlockedError`` after
    the chunks that were already released (none of them unchecked in buffer mode).
    """

    def __init__(self, stream: Any, guard: Guard, prompt: str) -> None:
        check, _, policy = guard._stream_checkers(prompt)
        self._stream = stream
        self._chunks = checked_stream(
            stream,
            check,
            strategy=policy.stream_strategy,
            every=policy.stream_check_every,
            emit="raw",
            on_cut="raise",
        )

    def __iter__(self) -> Iterator[Any]:
        return self._chunks

    def __next__(self) -> Any:
        return next(self._chunks)

    def __enter__(self) -> CheckedStream:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._chunks.close()
        close = getattr(self._stream, "close", None)
        if close is not None:
            close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


class AsyncCheckedStream:
    """Async ``CheckedStream`` for ``AsyncOpenAI`` / ``AsyncAnthropic`` streams."""

    def __init__(self, stream: Any, guard: Guard, prompt: str) -> None:
        _, acheck, policy = guard._stream_checkers(prompt)
        self._stream = stream
        self._chunks = achecked_stream(
            stream,
            acheck,
            strategy=policy.stream_strategy,
            every=policy.stream_check_every,
            emit="raw",
            on_cut="raise",
        )

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._chunks

    async def __anext__(self) -> Any:
        return await self._chunks.__anext__()

    async def __aenter__(self) -> AsyncCheckedStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._chunks.aclose()
        for name in ("aclose", "close"):
            close = getattr(self._stream, name, None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
                return

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


ToolCall = tuple[str, Any]  # (tool name, arguments)


def _parse_arguments(arguments: Any) -> Any:
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return arguments
    return arguments


def _items(value: Any) -> Iterable[Any]:
    return value if isinstance(value, list | tuple) else ()


def tool_calls_in(response: Any) -> list[ToolCall]:
    """The tool calls in an OpenAI (Chat Completions or Responses) or Anthropic response."""
    calls: list[ToolCall] = []
    for choice in _items(field(response, "choices")):  # OpenAI chat.completions
        for call in _items(field(field(choice, "message"), "tool_calls")):
            function = field(call, "function")
            name = str(field(function, "name") or "")
            calls.append((name, _parse_arguments(field(function, "arguments"))))
    for item in _items(field(response, "output")):  # OpenAI Responses API
        if field(item, "type") == "function_call":
            name = str(field(item, "name") or "")
            calls.append((name, _parse_arguments(field(item, "arguments"))))
    for block in _items(field(response, "content")):  # Anthropic messages
        if field(block, "type") == "tool_use":
            calls.append((str(field(block, "name") or ""), field(block, "input")))
    return calls


_WRAPPED = "__jev_guard_wrapped__"


def patch_create(
    resource: Any,
    guard: Guard,
    prompt_of: Callable[[dict[str, Any]], str],
    reply_of: Callable[[Any], str],
    tool_guard: ToolGuard | None = None,
) -> None:
    """Replace `resource.create` with a checked version (sync or async). Idempotent.

    With ``tool_guard``, every tool call in a (non-streamed) response is checked too, with
    the prompt as the user's request; a blocked call raises ``GuardBlockedError``.
    """
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
                return AsyncCheckedStream(response, guard, prompt)
            enforce(await guard.acheck_output(prompt, reply_of(response)))
            if tool_guard is not None:
                for name, arguments in tool_calls_in(response):
                    enforce(await tool_guard.acheck_tool_call(name, arguments, prompt or None))
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
            return CheckedStream(response, guard, prompt)
        enforce(guard.check_output(prompt, reply_of(response)))
        if tool_guard is not None:
            for name, arguments in tool_calls_in(response):
                enforce(tool_guard.check_tool_call(name, arguments, prompt or None))
        return response

    setattr(create, _WRAPPED, True)
    resource.create = create
