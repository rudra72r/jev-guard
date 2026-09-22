"""Output checks for streamed LLM responses.

Two strategies, chosen by the policy's ``stream_strategy``:

**buffer** (default). Chunks are held back until the text so far has been checked, then
released. Nothing the user sees was unchecked. Costs one Jev call (~70-500 ms) per
``stream_check_every`` chunks, and time-to-first-token grows by one check.

**rollback**. Chunks stream through immediately and checks run in the background. If a
check blocks, the stream stops and a ``StreamCut`` marker with ``retract=True`` tells the app
to remove what it already showed. Keeps time-to-first-token, but the user may briefly see
blocked text, so the app must handle the marker.

Every check covers the whole response so far (not just the newest chunks), so an
instruction split across chunks is still seen whole. A final check runs on the complete text
unless the last check already covered it. Cost therefore grows faster than length: about
``n_chunks / every`` checks averaging half the response each. Measured with the library's
token estimate at $0.042 per 1M tokens (general policy):

    300 chunks, every=40:   8 checks, $0.00014
    1000 chunks, every=40:  25 checks, $0.00089
    1000 chunks, every=100: 10 checks, $0.00037
    3000 chunks, every=40:  75 checks, $0.0066

For long outputs, raise ``stream_check_every`` in the policy.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import (
    AsyncGenerator,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    Generator,
    Iterable,
    Iterator,
    Mapping,
)
from typing import Any, Literal

from jev_guard.errors import GuardBlockedError
from jev_guard.types import Verdict

logger = logging.getLogger("jev_guard")

Strategy = Literal["buffer", "rollback"]
Emit = Literal["text", "raw"]
OnCut = Literal["marker", "raise"]
AsyncCheck = Callable[[str], Awaitable[Verdict]]
SyncCheck = Callable[[str], Verdict]

BUFFER_CUT_TEXT = "[Response stopped by the safety filter.]"
ROLLBACK_CUT_TEXT = "[Message removed by the safety filter.]"


class StreamCut(str):
    """The last item of a stream jev-guard stopped. A ``str``, so it also renders as a notice.

    ``verdict`` explains why. ``retract`` is True in rollback mode: the text already shown was
    not safe, so the app should replace the whole message (e.g. "message removed" UI).
    """

    verdict: Verdict
    retract: bool

    def __new__(cls, verdict: Verdict, *, retract: bool) -> StreamCut:
        text = (
            ROLLBACK_CUT_TEXT if retract else f"\n\n{verdict.suggested_response or BUFFER_CUT_TEXT}"
        )
        cut = super().__new__(cls, text)
        cut.verdict = verdict
        cut.retract = retract
        return cut


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def chunk_text(chunk: Any) -> str:  # noqa: PLR0911 (one return per SDK chunk shape)
    """The text in one stream item: a str, or an OpenAI / Anthropic / LangChain chunk."""
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, bytes):
        return chunk.decode("utf-8", errors="replace")
    choices = _get(chunk, "choices")
    if choices:  # OpenAI chat.completions chunk
        content = _get(_get(choices[0], "delta"), "content")
        return content if isinstance(content, str) else ""
    kind = _get(chunk, "type")
    if kind == "response.output_text.delta":  # OpenAI Responses API event
        delta = _get(chunk, "delta")
        return delta if isinstance(delta, str) else ""
    if kind == "content_block_delta":  # Anthropic messages stream event
        text = _get(_get(chunk, "delta"), "text")
        return text if isinstance(text, str) else ""
    content = _get(chunk, "content")  # LangChain AIMessageChunk and similar
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p if isinstance(p, str) else str(_get(p, "text") or "") for p in content)
    return ""


# --- async ----------------------------------------------------------------------------------


async def _aiter(stream: AsyncIterable[Any] | Iterable[Any]) -> AsyncIterator[Any]:
    if isinstance(stream, AsyncIterable):
        async for item in stream:
            yield item
    else:
        for item in stream:
            yield item


async def _aclose(stream: Any) -> None:
    close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
    if close is None:
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception:  # closing is best-effort; the cut must still happen
        logger.debug("jev-guard: closing the upstream stream failed", exc_info=True)


async def achecked_stream(  # noqa: PLR0913 (keyword-only knobs)
    stream: AsyncIterable[Any] | Iterable[Any],
    check: AsyncCheck,
    *,
    strategy: Strategy = "buffer",
    every: int = 40,
    emit: Emit = "text",
    on_cut: OnCut = "marker",
) -> AsyncGenerator[Any, None]:
    """Yield the stream's items, checking the accumulated text as it goes.

    ``emit="text"`` yields text strings (empty chunks dropped); ``"raw"`` yields the original
    chunk objects. On a block, ``on_cut="marker"`` yields a ``StreamCut`` and stops;
    ``"raise"`` raises ``GuardBlockedError`` (for callers that expect chunk objects).
    """
    driver = _arollback if strategy == "rollback" else _abuffer
    inner = driver(stream, check, max(1, every), emit, on_cut)
    try:
        async for item in inner:
            yield item
    finally:
        # `async for` doesn't close an abandoned async generator. Without this, a consumer
        # that stops early (user closed the tab) leaves a background Jev check running.
        await inner.aclose()


def _emit(chunk: Any, text: str, emit: Emit) -> Iterator[Any]:
    if emit == "raw":
        yield chunk
    elif text:
        yield text


def _cut(verdict: Verdict, *, retract: bool, on_cut: OnCut) -> StreamCut:
    if on_cut == "raise":
        raise GuardBlockedError(verdict)
    return StreamCut(verdict, retract=retract)


async def _abuffer(
    stream: AsyncIterable[Any] | Iterable[Any],
    check: AsyncCheck,
    every: int,
    emit: Emit,
    on_cut: OnCut,
) -> AsyncGenerator[Any, None]:
    released = ""
    pending: list[tuple[Any, str]] = []
    unchecked = 0  # text-bearing chunks in `pending` not yet covered by a check
    async for chunk in _aiter(stream):
        text = chunk_text(chunk)
        pending.append((chunk, text))
        unchecked += bool(text)
        if unchecked < every:
            continue
        candidate = released + "".join(t for _, t in pending)
        verdict = await check(candidate)
        if verdict.blocked:
            await _aclose(stream)
            yield _cut(verdict, retract=False, on_cut=on_cut)
            return
        for raw, piece in pending:
            for item in _emit(raw, piece, emit):
                yield item
        released, pending, unchecked = candidate, [], 0
    if unchecked:
        verdict = await check(released + "".join(t for _, t in pending))
        if verdict.blocked:
            yield _cut(verdict, retract=False, on_cut=on_cut)
            return
    for raw, piece in pending:
        for item in _emit(raw, piece, emit):
            yield item


async def _arollback(
    stream: AsyncIterable[Any] | Iterable[Any],
    check: AsyncCheck,
    every: int,
    emit: Emit,
    on_cut: OnCut,
) -> AsyncGenerator[Any, None]:
    parts: list[str] = []
    covered = 0  # len(parts) the latest started check covers
    since_check = 0
    task: asyncio.Future[Verdict] | None = None
    try:
        async for chunk in _aiter(stream):
            text = chunk_text(chunk)
            for item in _emit(chunk, text, emit):
                yield item
            if text:
                parts.append(text)
                since_check += 1
            if task is not None and task.done():
                verdict = task.result()
                task = None
                if verdict.blocked:
                    await _aclose(stream)
                    yield _cut(verdict, retract=True, on_cut=on_cut)
                    return
            if task is None and since_check >= every:
                covered, since_check = len(parts), 0
                task = asyncio.ensure_future(check("".join(parts)))
        if task is not None:
            verdict = await task
            task = None
            if verdict.blocked:
                yield _cut(verdict, retract=True, on_cut=on_cut)
                return
        if len(parts) > covered:
            verdict = await check("".join(parts))
            if verdict.blocked:
                yield _cut(verdict, retract=True, on_cut=on_cut)
    finally:
        if task is not None and not task.done():
            task.cancel()


# --- sync -----------------------------------------------------------------------------------


def _close(stream: Any) -> None:
    close = getattr(stream, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:
        logger.debug("jev-guard: closing the upstream stream failed", exc_info=True)


def checked_stream(  # noqa: PLR0913 (keyword-only knobs)
    stream: Iterable[Any],
    check: SyncCheck,
    *,
    strategy: Strategy = "buffer",
    every: int = 40,
    emit: Emit = "raw",
    on_cut: OnCut = "raise",
) -> Generator[Any, None, None]:
    """Synchronous ``achecked_stream``. In rollback mode checks run inline (no background
    thread), so the stream pauses briefly at each check point instead of at the start."""
    every = max(1, every)
    if strategy == "rollback":
        yield from _rollback(stream, check, every, emit, on_cut)
    else:
        yield from _buffer(stream, check, every, emit, on_cut)


def _buffer(
    stream: Iterable[Any], check: SyncCheck, every: int, emit: Emit, on_cut: OnCut
) -> Iterator[Any]:
    released = ""
    pending: list[tuple[Any, str]] = []
    unchecked = 0
    for chunk in stream:
        text = chunk_text(chunk)
        pending.append((chunk, text))
        unchecked += bool(text)
        if unchecked < every:
            continue
        candidate = released + "".join(t for _, t in pending)
        verdict = check(candidate)
        if verdict.blocked:
            _close(stream)
            yield _cut(verdict, retract=False, on_cut=on_cut)
            return
        for raw, piece in pending:
            yield from _emit(raw, piece, emit)
        released, pending, unchecked = candidate, [], 0
    if unchecked:
        verdict = check(released + "".join(t for _, t in pending))
        if verdict.blocked:
            yield _cut(verdict, retract=False, on_cut=on_cut)
            return
    for raw, piece in pending:
        yield from _emit(raw, piece, emit)


def _rollback(
    stream: Iterable[Any], check: SyncCheck, every: int, emit: Emit, on_cut: OnCut
) -> Iterator[Any]:
    parts: list[str] = []
    since_check = 0
    for chunk in stream:
        text = chunk_text(chunk)
        yield from _emit(chunk, text, emit)
        if not text:
            continue
        parts.append(text)
        since_check += 1
        if since_check >= every:
            since_check = 0
            verdict = check("".join(parts))
            if verdict.blocked:
                _close(stream)
                yield _cut(verdict, retract=True, on_cut=on_cut)
                return
    if since_check:
        verdict = check("".join(parts))
        if verdict.blocked:
            yield _cut(verdict, retract=True, on_cut=on_cut)
