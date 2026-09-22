"""PII redaction: regex pass, Luhn filtering, span bookkeeping, and the strict Jev pass."""

from __future__ import annotations

import pytest

import jev_guard.guard as guard_module
from jev_guard import aredact, redact
from jev_guard.redact import MAX_JEV_CHUNKS, _luhn_ok, _sentences


def fast(text):
    return redact(text, level="fast")


def test_docstring_example():
    clean, found = fast("Mail jane@example.com or call 555-0147")
    assert clean == "Mail [EMAIL] or call [PHONE]"
    assert found == [
        {"type": "email", "span": (5, 21), "value": "[EMAIL]"},
        {"type": "phone", "span": (30, 38), "value": "[PHONE]"},
    ]


@pytest.mark.parametrize(
    ("text", "expected", "kind"),
    [
        ("reach me at a.b+tag@mail.example.co.uk today", "reach me at [EMAIL] today", "email"),
        ("SSN 123-45-6789.", "SSN [SSN].", "ssn"),
        ("card 4111 1111 1111 1111 exp", "card [CARD] exp", "credit_card"),
        ("card 4111-1111-1111-1111", "card [CARD]", "credit_card"),
        ("amex 378282246310005", "amex [CARD]", "credit_card"),
        ("call (212) 555-0147 now", "call [PHONE] now", "phone"),
        ("call 212.555.0147", "call [PHONE]", "phone"),
        ("call +44 20 7946 0958", "call [PHONE]", "phone"),
        ("call +1 555-0139", "call [PHONE]", "phone"),
    ],
)
def test_regex_patterns(text, expected, kind):
    clean, found = fast(text)
    assert clean == expected
    assert [f["type"] for f in found] == [kind]


@pytest.mark.parametrize(
    "text",
    [
        "order 1042 shipped",
        "tracking 1234567890123 (fails Luhn)",
        "version 3.12.10",
        "call me in 2026-09-22",
        "SKU AB-123-45-6789X",
        "costs $1,234.56",
    ],
)
def test_no_false_positives_on_ordinary_numbers(text):
    assert fast(text) == (text, [])


def test_spans_index_the_original_text():
    text = "a@example.com, SSN 000-12-3456, card 4111111111111111"
    clean, found = fast(text)
    assert clean == "[EMAIL], SSN [SSN], card [CARD]"
    for item in found:
        start, end = item["span"]
        assert text[start:end] not in clean
        assert item["value"] in clean


def test_found_never_contains_original_values():
    text = "jane@example.com 123-45-6789"
    _, found = fast(text)
    assert all("jane" not in str(f) and "6789" not in str(f) for f in found)


def test_ssn_wins_over_phone_overlap():
    _, found = fast("id 123-45-6789")
    assert [f["type"] for f in found] == ["ssn"]


def test_luhn():
    assert _luhn_ok("4111 1111 1111 1111")
    assert not _luhn_ok("4111 1111 1111 1112")
    assert not _luhn_ok("123")


def test_bad_level():
    with pytest.raises(ValueError, match="'fast' or 'strict'"):
        redact("x", level="paranoid")  # type: ignore[arg-type]


# --- strict ---------------------------------------------------------------------------------


@pytest.fixture
def jev_flags_names(fake_jev):
    """Flags any sentence mentioning 'Jordan' (a stand-in for Jev spotting a name)."""
    original = fake_jev.evaluate

    def evaluate(state, questions):
        for key, sentence in state.items():
            fake_jev.noul(key, 0.9 if "Jordan" in sentence else 0.05)
        return original(state, questions)

    fake_jev.evaluate = evaluate
    return fake_jev


def test_strict_redacts_flagged_sentences_whole(jev_flags_names):
    text = "Thanks for the update. Jordan Reyes lives on Elm Street. Ship it Monday."
    clean, found = redact(text)
    assert clean == "Thanks for the update. [REDACTED] Ship it Monday."
    assert [f["type"] for f in found] == ["custom"]
    assert len(jev_flags_names.calls) == 1  # one Jev call for the whole text


def test_strict_sends_sentences_with_regex_hits_already_masked(jev_flags_names):
    text = "Contact Jordan at jordan@example.com. Nothing else."
    clean, found = redact(text)
    state = jev_flags_names.calls[0][0]
    assert state["s0"] == "Contact Jordan at [EMAIL]."
    assert "jordan@example.com" not in str(state)  # the raw value never reaches Jev
    assert clean == "[REDACTED] Nothing else."
    assert [f["type"] for f in found] == ["custom"]  # the sentence replaces the inner email


def test_strict_keeps_regex_hits_in_unflagged_sentences(jev_flags_names):
    clean, found = redact("Email ops@example.com for help.")
    assert clean == "Email [EMAIL] for help."
    assert [f["type"] for f in found] == ["email"]


def test_strict_on_text_without_words_makes_no_call(fake_jev):
    assert redact("   ") == ("   ", [])
    clean, found = redact("... 555-0147")  # no sentence with letters to ask Jev about
    assert clean == "... [PHONE]"
    assert [f["type"] for f in found] == ["phone"]
    assert fake_jev.calls == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("One. Two!  Three?", ["One.", "Two!", "Three?"]),
        ("mail a@example.com now. Next", ["mail a@example.com now.", "Next"]),
        ("costs 3.5 dollars. ok", ["costs 3.5 dollars.", "ok"]),
        ("  line one\n\n  line two  ", ["line one", "line two"]),
    ],
)
def test_sentence_splitting(text, expected):
    assert [text[s:e] for s, e in _sentences(text)] == expected


def test_strict_groups_long_text_into_at_most_32_chunks(jev_flags_names):
    text = " ".join(f"Sentence number {i} is here." for i in range(100)) + " Jordan was here."
    clean, _ = redact(text)
    state, questions = jev_flags_names.calls[0]
    assert len(state) == len(questions) <= MAX_JEV_CHUNKS
    assert clean.endswith("[REDACTED]")
    assert clean.startswith("Sentence number 0")


def test_strict_questions_are_nouls_about_their_own_chunk(jev_flags_names):
    redact("Hello there. Jordan called.")
    questions = jev_flags_names.calls[0][1]
    assert set(questions) == {"s0", "s1"}
    assert questions["s1"]["type"] == "noul"
    assert "s1" in questions["s1"]["instructions"]


async def test_aredact(jev_flags_names):
    clean, found = await aredact("Jordan called. Fine.")
    assert clean == "[REDACTED] Fine."
    assert await aredact("x@example.com", level="fast") == (
        "[EMAIL]",
        [{"type": "email", "span": (0, 13), "value": "[EMAIL]"}],
    )
    assert await aredact("") == ("", [])
    clean, found = await aredact("555-0147")
    assert (clean, len(found)) == ("[PHONE]", 1)


def test_backend_is_reused_and_follows_factory_changes(fake_jev, monkeypatch):
    redact("Hello there.")
    redact("Hello again.")
    assert len(fake_jev.calls) == 2

    class Other(type(fake_jev)):
        pass

    other = Other()
    monkeypatch.setattr(guard_module, "_backend_factory", lambda: other)
    redact("Third one.")
    assert len(other.calls) == 1
