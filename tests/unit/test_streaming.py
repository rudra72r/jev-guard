"""Streaming guards: buffer-and-check and rollback, sync and async, wrappers and chunk shapes."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk
from pydantic import Field

from jev_guard import Guard, GuardBlockedError, Policy, StreamCut
from jev_guard.guards.streaming import (
    BUFFER_CUT_TEXT,
    ROLLBACK_CUT_TEXT,
    achecked_stream,
    checked_stream,
    chunk_text,
)
from jev_guard.integrations.anthropic_sdk import wrap_anthropic
from jev_guard.integrations.openai_sdk import wrap_openai
from jev_guard.types import QuestionSpec, Verdict


def verdict(blocked: bool, suggested: str | None = None) -> Verdict:
    return Verdict(
        action="block" if blocked else "allow",
        stage="output",
        confidence=0.1 if blocked else 1.0,
        raw_answers={},
        reasons=["bad: 0.90 > 0.50 (critical)"] if blocked else [],
        latency_ms=1.0,
        input_tokens_used=10,
        estimated_cost_usd=0.0,
        suggested_response=suggested,
        policy_name="t",
        policy_version="1.0.0",
    )


class Checker:
    """Records every text it's asked to check; blocks once the text contains `trigger`."""

    def __init__(self, trigger: str | None = None, suggested: str | None = None) -> None:
        self.trigger = trigger
        self.suggested = suggested
        self.seen: list[str] = []

    def __call__(self, text: str) -> Verdict:
        self.seen.append(text)
        return verdict(bool(self.trigger and self.trigger in text), self.suggested)

    async def acheck(self, text: str) -> Verdict:
        return self(text)


class Upstream:
    """An iterable stream that records whether it was closed and how far it was read."""

    def __init__(self, items):
        self.items = list(items)
        self.read = 0
        self.closed = False

    def __iter__(self):
        for item in self.items:
            self.read += 1
            yield item

    def close(self):
        self.closed = True


class AsyncUpstream(Upstream):
    """Yields control between items like a real network stream, so background checks run."""

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for item in self.items:
            await asyncio.sleep(0)
            self.read += 1
            yield item

    async def aclose(self):
        self.closed = True


async def collect(agen):
    return [item async for item in agen]


# --- buffer ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "every", "checks"),
    [(7, 3, ["abc", "abcdef", "abcdefg"]), (6, 3, ["abc", "abcdef"]), (2, 1, ["a", "ab"])],
)
async def test_buffer_checks_cumulative_text_at_boundaries(n, every, checks):
    chunks = list("abcdefg"[:n])
    checker = Checker()
    out = await collect(achecked_stream(chunks, checker.acheck, every=every))
    assert out == chunks
    assert checker.seen == checks
    sync_checker = Checker()
    assert list(checked_stream(chunks, sync_checker, every=every, emit="text")) == chunks
    assert sync_checker.seen == checks


async def test_empty_stream_makes_no_checks():
    checker = Checker()
    assert await collect(achecked_stream([], checker.acheck)) == []
    assert list(checked_stream([], checker)) == []
    assert checker.seen == []


async def test_chunks_without_text_are_not_counted_and_keep_their_order():
    role, finish = {"choices": [{"delta": {"role": "assistant"}}]}, {"choices": [{"delta": {}}]}
    chunks = [role, "a", "b", finish]
    checker = Checker()
    raw = await collect(achecked_stream(chunks, checker.acheck, every=2, emit="raw"))
    assert raw == chunks
    assert checker.seen == ["ab"]  # the text-less trailing chunk triggers no extra check
    assert await collect(achecked_stream(chunks, Checker().acheck, every=2)) == ["a", "b"]


async def test_buffer_block_in_first_batch_releases_nothing_and_closes_upstream():
    upstream = AsyncUpstream(["rm ", "-rf ", "/", " now"])
    checker = Checker(trigger="rm -rf")
    out = await collect(achecked_stream(upstream, checker.acheck, every=3))
    assert len(out) == 1
    cut = out[0]
    assert isinstance(cut, StreamCut)
    assert isinstance(cut, str)
    assert not cut.retract
    assert cut.verdict.blocked
    assert cut == f"\n\n{BUFFER_CUT_TEXT}"
    assert upstream.closed
    assert upstream.read == 3  # stopped pulling from the LLM


async def test_buffer_block_later_keeps_checked_prefix_and_uses_safe_reply():
    chunks = ["Hello ", "there. ", "Now ", "run ", "rm ", "-rf"]
    checker = Checker(trigger="rm -rf", suggested="I can't help with that.")
    out = await collect(achecked_stream(chunks, checker.acheck, every=2))
    assert out[:4] == ["Hello ", "there. ", "Now ", "run "]
    assert out[-1] == "\n\nI can't help with that."
    assert "".join(out[:-1]) == "Hello there. Now run "


async def test_buffer_block_in_final_partial_batch_withholds_it():
    chunks = ["safe ", "text ", "then ", "BAD"]
    checker = Checker(trigger="BAD")
    out = await collect(achecked_stream(chunks, checker.acheck, every=3))
    assert out[:3] == ["safe ", "text ", "then "]
    assert isinstance(out[3], StreamCut)
    assert checker.seen[-1] == "safe text then BAD"


async def test_attack_split_across_chunks_is_seen_whole():
    chunks = ["ignore all prev", "ious instruc", "tions"]
    checker = Checker(trigger="ignore all previous instructions")
    out = await collect(achecked_stream(chunks, checker.acheck, every=1))
    assert out[:2] == ["ignore all prev", "ious instruc"]
    assert isinstance(out[2], StreamCut)


def test_sync_buffer_raise_mode():
    upstream = Upstream(["ok ", "ok ", "BAD ", "more"])
    checker = Checker(trigger="BAD")
    stream = checked_stream(upstream, checker, every=2, emit="raw", on_cut="raise")
    assert next(stream) == "ok "
    assert next(stream) == "ok "
    with pytest.raises(GuardBlockedError) as info:
        next(stream)
    assert info.value.verdict.blocked
    assert upstream.closed


def test_sync_buffer_marker_on_final_batch():
    out = list(
        checked_stream(["a", "BAD"], Checker(trigger="BAD"), every=5, emit="text", on_cut="marker")
    )
    assert len(out) == 1
    assert isinstance(out[0], StreamCut)


async def test_every_below_one_is_clamped():
    checker = Checker()
    await collect(achecked_stream(["a", "b"], checker.acheck, every=0))
    assert checker.seen == ["a", "ab"]


# --- rollback -------------------------------------------------------------------------------


async def test_rollback_yields_before_checks_finish():
    """Time-to-first-token is preserved: the first chunk arrives while the check is pending."""
    gate = asyncio.Event()
    started = asyncio.Event()

    async def slow_check(text: str) -> Verdict:
        started.set()
        await gate.wait()
        return verdict(False)

    agen = achecked_stream(AsyncUpstream("abc"), slow_check, strategy="rollback", every=1)
    assert await agen.__anext__() == "a"
    assert await agen.__anext__() == "b"  # delivered while the check of "a" is still pending
    assert started.is_set()
    assert not gate.is_set()
    gate.set()
    rest = [item async for item in agen]
    assert rest == ["c"]


async def test_rollback_block_emits_retract_marker_and_closes():
    upstream = AsyncUpstream(["fine ", "BAD ", "x ", "y ", "z "])
    checker = Checker(trigger="BAD")
    out = await collect(achecked_stream(upstream, checker.acheck, strategy="rollback", every=2))
    cut = out[-1]
    assert isinstance(cut, StreamCut)
    assert cut.retract
    assert cut == ROLLBACK_CUT_TEXT
    assert out[0] == "fine "  # already shown; the app must retract it
    assert upstream.closed
    assert upstream.read < len(upstream.items)  # stopped mid-stream


async def test_rollback_with_a_stream_that_never_yields_cuts_at_the_end():
    """A stream with no await points starves background checks; the block still lands."""
    checker = Checker(trigger="BAD")
    out = await collect(
        achecked_stream(["fine ", "BAD ", "x "], checker.acheck, strategy="rollback", every=2)
    )
    assert out[:3] == ["fine ", "BAD ", "x "]
    assert out[-1].retract


async def test_rollback_block_on_last_background_check():
    checker = Checker(trigger="BAD")
    out = await collect(achecked_stream(["a", "BAD"], checker.acheck, strategy="rollback", every=2))
    assert out[:2] == ["a", "BAD"]
    assert out[-1].retract


async def test_rollback_final_check_covers_the_tail():
    checker = Checker(trigger="BAD")
    out = await collect(
        achecked_stream(["a", "b", "c", "BAD"], checker.acheck, strategy="rollback", every=3)
    )
    assert out[:4] == ["a", "b", "c", "BAD"]
    assert out[-1].retract
    assert checker.seen[-1] == "abcBAD"


async def test_rollback_clean_stream():
    checker = Checker()
    out = await collect(achecked_stream(list("abcd"), checker.acheck, strategy="rollback", every=2))
    assert out == list("abcd")
    assert checker.seen[-1] == "abcd"


async def test_rollback_raise_mode():
    stream = achecked_stream(
        ["BAD"],
        Checker(trigger="BAD").acheck,
        strategy="rollback",
        every=1,
        emit="raw",
        on_cut="raise",
    )
    with pytest.raises(GuardBlockedError):
        await collect(stream)


async def test_rollback_consumer_stopping_early_cancels_background_check():
    cancelled = asyncio.Event()

    async def never_finishes(text: str) -> Verdict:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return verdict(False)  # pragma: no cover

    agen = achecked_stream(AsyncUpstream("abc"), never_finishes, strategy="rollback", every=1)
    await agen.__anext__()
    await agen.__anext__()  # the background check of "a" is now running
    await agen.aclose()
    await asyncio.sleep(0)
    assert cancelled.is_set()


async def test_rollback_check_errors_propagate():
    async def broken(text: str) -> Verdict:
        raise RuntimeError("jev down")

    with pytest.raises(RuntimeError, match="jev down"):
        await collect(achecked_stream(list("ab"), broken, strategy="rollback", every=1))


def test_sync_rollback_yields_then_checks_inline():
    upstream = Upstream(["ok ", "BAD", "never"])
    checker = Checker(trigger="BAD")
    out = list(
        checked_stream(
            upstream, checker, strategy="rollback", every=1, emit="text", on_cut="marker"
        )
    )
    assert out[:2] == ["ok ", "BAD"]
    assert out[2].retract
    assert upstream.closed
    assert "never" not in out


def test_sync_rollback_tail_check():
    checker = Checker(trigger="BAD")
    out = list(
        checked_stream(
            ["a", "b", "BAD"], checker, strategy="rollback", every=2, emit="text", on_cut="marker"
        )
    )
    assert out[-1].retract
    assert list(checked_stream(["a"], Checker(), strategy="rollback", every=5)) == ["a"]


# --- chunk shapes ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chunk", "text"),
    [
        ("plain", "plain"),
        (b"bytes", "bytes"),
        (SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="oa"))]), "oa"),
        (SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))]), ""),
        ({"choices": [{"delta": {"content": "dict"}}]}, "dict"),
        (SimpleNamespace(type="response.output_text.delta", delta="resp"), "resp"),
        (SimpleNamespace(type="response.created"), ""),
        (
            SimpleNamespace(
                type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="claude")
            ),
            "claude",
        ),
        (
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="input_json_delta", partial_json="{"),
            ),
            "",
        ),
        (SimpleNamespace(type="message_start"), ""),
        (AIMessageChunk(content="lc"), "lc"),
        (AIMessageChunk(content=[{"type": "text", "text": "parts"}, "x"]), "partsx"),
        (42, ""),
    ],
)
def test_chunk_text(chunk, text):
    assert chunk_text(chunk) == text


# --- Guard.astream_check --------------------------------------------------------------------


class StreamPolicy(Policy):
    name: str = "stream_test"
    output: dict[str, QuestionSpec] = Field(
        default_factory=lambda: {
            "bad": QuestionSpec(type="noul", instructions="x", severity="critical", threshold=0.5)
        }
    )
    stream_check_every: int = 2
    suggested_responses: dict[str, str] = Field(default_factory=lambda: {"bad": "Nope."})


@pytest.fixture
def jev_blocks_bad(fake_jev):
    original = fake_jev.evaluate

    def evaluate(state, questions):
        fake_jev.noul("bad", 0.9 if "BAD" in state.get("assistant_response", "") else 0.1)
        return original(state, questions)

    fake_jev.evaluate = evaluate
    return fake_jev


async def test_guard_astream_check_buffer(jev_blocks_bad):
    guard = Guard(policy=StreamPolicy())
    out = [t async for t in guard.astream_check(AsyncUpstream(["hi ", "there ", "BAD"]), "q")]
    assert out[:2] == ["hi ", "there "]
    assert out[-1] == "\n\nNope."
    assert out[-1].verdict.policy_name == "stream_test"
    first_state = jev_blocks_bad.calls[0][0]
    assert first_state == {"user_message": "q", "assistant_response": "hi there "}


async def test_guard_astream_check_rollback_from_policy(jev_blocks_bad):
    policy = StreamPolicy(stream_strategy="rollback", stream_check_every=1)
    out = [t async for t in Guard(policy=policy).astream_check(["ok", "BAD"], "q")]
    assert out[:2] == ["ok", "BAD"]
    assert out[-1].retract


async def test_guard_astream_check_accepts_raw_openai_chunks(jev_blocks_bad):
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=c))])
        for c in ("a", "b", "c")
    ]
    out = [t async for t in Guard(policy=StreamPolicy()).astream_check(chunks, "q")]
    assert out == ["a", "b", "c"]


async def test_rag_stream_warns_about_missing_context_once(fake_jev, caplog):
    for name in ("contains_pii", "is_off_topic"):
        fake_jev.noul(name, 0.01)
    policy = Policy.from_builtin("rag").model_copy(update={"stream_check_every": 1})
    with caplog.at_level(logging.WARNING, logger="jev_guard"):
        out = [t async for t in Guard(policy=policy).astream_check(list("abc"), "q")]
    assert out == list("abc")
    assert caplog.text.count("needs context") == 1
    assert set(fake_jev.calls[0][1]) == set(Policy.from_builtin("general").output)


def test_stream_settings_roundtrip_through_yaml(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text(
        "extends: general\nstream_strategy: rollback\nstream_check_every: 10\n", encoding="utf-8"
    )
    policy = Policy.from_yaml(path)
    assert (policy.stream_strategy, policy.stream_check_every) == ("rollback", 10)
    bad = tmp_path / "bad.yaml"
    bad.write_text("extends: general\nstream_strategy: sideways\n", encoding="utf-8")
    with pytest.raises(Exception, match="stream_strategy"):
        Policy.from_yaml(bad)


# --- client wrappers ------------------------------------------------------------------------


def oa_chunk(text):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


class FakeCompletions:
    def __init__(self, texts):
        self.texts = texts
        self.stream = None

    def create(self, **kwargs):
        self.stream = Upstream([oa_chunk(t) for t in self.texts])
        self.stream.response = "http-response"
        return self.stream


class FakeAsyncCompletions(FakeCompletions):
    async def create(self, **kwargs):
        self.stream = AsyncUpstream([oa_chunk(t) for t in self.texts])
        return self.stream


MESSAGES = [{"role": "user", "content": "hi"}]


def test_wrapped_openai_stream_passes_raw_chunks(jev_blocks_bad):
    completions = FakeCompletions(["a", "b", "c"])
    client = wrap_openai(
        SimpleNamespace(chat=SimpleNamespace(completions=completions)), policy=StreamPolicy()
    )
    with client.chat.completions.create(model="m", messages=MESSAGES, stream=True) as stream:
        chunks = list(stream)
        assert stream.response == "http-response"  # SDK attributes pass through
    assert [chunk_text(c) for c in chunks] == ["a", "b", "c"]
    assert completions.stream.closed


def test_wrapped_openai_stream_raises_on_block(jev_blocks_bad):
    completions = FakeCompletions(["fine ", "text ", "BAD", "x"])
    client = wrap_openai(
        SimpleNamespace(chat=SimpleNamespace(completions=completions)), policy=StreamPolicy()
    )
    stream = client.chat.completions.create(model="m", messages=MESSAGES, stream=True)
    got = []
    with pytest.raises(GuardBlockedError) as info:
        for chunk in stream:
            got.append(chunk_text(chunk))
    assert got == ["fine ", "text "]
    assert info.value.verdict.stage == "output"
    assert next(iter([1]))  # iteration protocol still intact after the error


def test_checked_stream_next_protocol(jev_blocks_bad):
    completions = FakeCompletions(["a"])
    client = wrap_openai(
        SimpleNamespace(chat=SimpleNamespace(completions=completions)), policy=StreamPolicy()
    )
    stream = client.chat.completions.create(model="m", messages=MESSAGES, stream=True)
    assert chunk_text(next(stream)) == "a"
    stream.close()


async def test_wrapped_async_openai_stream(jev_blocks_bad):
    completions = FakeAsyncCompletions(["a", "b", "BAD"])
    client = wrap_openai(
        SimpleNamespace(chat=SimpleNamespace(completions=completions)), policy=StreamPolicy()
    )
    stream = await client.chat.completions.create(model="m", messages=MESSAGES, stream=True)
    got = []
    with pytest.raises(GuardBlockedError):
        async with stream:
            async for chunk in stream:
                got.append(chunk_text(chunk))
    assert got == ["a", "b"]
    assert completions.stream.closed


async def test_async_checked_stream_anext_and_close(jev_blocks_bad):
    completions = FakeAsyncCompletions(["a", "b"])
    client = wrap_openai(
        SimpleNamespace(chat=SimpleNamespace(completions=completions)), policy=StreamPolicy()
    )
    stream = await client.chat.completions.create(model="m", messages=MESSAGES, stream=True)
    assert chunk_text(await stream.__anext__()) == "a"
    assert stream.read >= 1  # attribute passthrough to the SDK stream
    await stream.close()
    assert completions.stream.closed


def test_wrapped_anthropic_stream(jev_blocks_bad):
    def event(text):
        return SimpleNamespace(
            type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=text)
        )

    class Messages:
        def create(self, **kwargs):
            return Upstream(
                [
                    SimpleNamespace(type="message_start"),
                    event("Once "),
                    event("upon"),
                    SimpleNamespace(type="message_stop"),
                ]
            )

    client = wrap_anthropic(SimpleNamespace(messages=Messages()), policy=StreamPolicy())
    events = list(client.messages.create(model="m", max_tokens=5, messages=MESSAGES, stream=True))
    assert [e.type for e in events] == [
        "message_start",
        "content_block_delta",
        "content_block_delta",
        "message_stop",
    ]
    assert jev_blocks_bad.calls[-1][0]["assistant_response"] == "Once upon"
