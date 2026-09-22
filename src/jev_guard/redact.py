"""PII redaction: regex for well-known formats, then (``strict``) one Jev call for the rest.

    from jev_guard import redact

    clean, found = redact("Mail jane@example.com or call 555-0147", level="fast")
    # clean == "Mail [EMAIL] or call [PHONE]"
    # found == [{"type": "email", "span": (5, 21), "value": "[EMAIL]"},
    #           {"type": "phone", "span": (30, 38), "value": "[PHONE]"}]

Accuracy tradeoff:

- **fast**: regex only. Emails, phone numbers, US SSNs, and payment card numbers (Luhn-checked
  so order numbers survive). No network, no cost, deterministic. Misses names, street
  addresses, and other free-form personal data.
- **strict**: fast, plus one Jev call asking whether each sentence (with regex hits already
  masked) still identifies a person. Flagged sentences are replaced whole with
  ``[REDACTED]``. Jev classifies text, it doesn't locate words, so strict redaction is
  sentence-granular: it over-redacts rather than leaving a name next to a masked email.

``found`` never contains the original values, only what replaced them. Spans index into the
*original* text. Not a compliance guarantee: see the README's "Not a silver bullet".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypedDict

from jev_guard.client import JevBackend, JevResult

PiiType = Literal["email", "phone", "ssn", "credit_card", "custom"]
Level = Literal["fast", "strict"]

PLACEHOLDERS: dict[PiiType, str] = {
    "email": "[EMAIL]",
    "phone": "[PHONE]",
    "ssn": "[SSN]",
    "credit_card": "[CARD]",
    "custom": "[REDACTED]",
}
STRICT_THRESHOLD = 0.6
MAX_JEV_CHUNKS = 32
_MIN_CARD_DIGITS = 13
_E164_DIGITS = (8, 15)  # plausible international phone number length, country code included


class Found(TypedDict):
    type: PiiType
    span: tuple[int, int]
    value: str


def _luhn_ok(candidate: str) -> bool:
    digits = [int(c) for c in candidate if c.isdigit()]
    if len(digits) < _MIN_CARD_DIGITS:
        return False
    total = 0
    for i, digit in enumerate(reversed(digits)):
        value = digit * 2 if i % 2 else digit
        total += value - 9 if value > 9 else value  # noqa: PLR2004 (Luhn digit fold)
    return total % 10 == 0


# Order matters: earlier patterns win overlaps (an SSN is never re-read as a phone number).
_PATTERNS: list[tuple[PiiType, re.Pattern[str], Callable[[str], bool] | None]] = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}"), None),
    ("ssn", re.compile(r"(?<![\w-])\d{3}-\d{2}-\d{4}(?![\w-])"), None),
    ("credit_card", re.compile(r"(?<![\w-])\d(?:[ -]?\d){12,18}(?![\w-])"), _luhn_ok),
    (
        "phone",
        re.compile(
            r"(?<![\w+])"
            r"(?:\+\d{1,3}[\s.-]?)?"  # country code
            r"(?:\(\d{3}\)\s?|\d{3}[\s.-])?"  # area code
            r"\d{3}[\s.-]\d{4}"
            r"(?![\w-])"
        ),
        None,
    ),
    # International numbers with other groupings, e.g. +44 20 7946 0958 (8-15 digits, E.164).
    (
        "phone",
        re.compile(r"(?<![\w+])\+\d{1,3}(?:[\s.-]?\d{1,4}){2,5}(?![\w-])"),
        lambda m: _E164_DIGITS[0] <= sum(c.isdigit() for c in m) <= _E164_DIGITS[1],
    ),
]
# Sentence ends: .!? followed by whitespace, or a newline. A bare "." (emails, URLs, 3.5)
# is not a boundary.
_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\s*\n\s*")


@dataclass(frozen=True, slots=True)
class _Hit:
    type: PiiType
    start: int
    end: int


def _regex_hits(text: str) -> list[_Hit]:
    hits: list[_Hit] = []
    for pii_type, pattern, valid in _PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < h.end and h.start < end for h in hits):
                continue
            if valid is not None and not valid(match.group()):
                continue
            hits.append(_Hit(pii_type, start, end))
    return sorted(hits, key=lambda h: h.start)


def _apply(text: str, hits: list[_Hit]) -> tuple[str, list[Found]]:
    out: list[str] = []
    found: list[Found] = []
    cursor = 0
    for hit in sorted(hits, key=lambda h: h.start):
        out.append(text[cursor : hit.start])
        placeholder = PLACEHOLDERS[hit.type]
        out.append(placeholder)
        found.append({"type": hit.type, "span": (hit.start, hit.end), "value": placeholder})
        cursor = hit.end
    out.append(text[cursor:])
    return "".join(out), found


# --- strict mode --------------------------------------------------------------------------


def _sentences(text: str) -> list[tuple[int, int]]:
    """Trimmed sentence spans (no surrounding whitespace)."""
    spans = []
    start = 0
    for boundary in [*_BOUNDARY.finditer(text), None]:
        end = boundary.start() if boundary else len(text)
        chunk = text[start:end]
        stripped = chunk.strip()
        if stripped:
            left = start + (len(chunk) - len(chunk.lstrip()))
            spans.append((left, left + len(stripped)))
        if boundary:
            start = boundary.end()
    return spans


def _chunks(text: str) -> list[tuple[int, int]]:
    """Sentence spans with at least two letters, grouped so there are at most MAX_JEV_CHUNKS."""
    spans = [(s, e) for s, e in _sentences(text) if sum(c.isalpha() for c in text[s:e]) > 1]
    if len(spans) <= MAX_JEV_CHUNKS:
        return spans
    size = -(-len(spans) // MAX_JEV_CHUNKS)  # ceil division
    return [
        (group[0][0], group[-1][1])
        for group in (spans[i : i + size] for i in range(0, len(spans), size))
    ]


def _masked(text: str, start: int, end: int, hits: list[_Hit]) -> str:
    inside = [
        _Hit(h.type, h.start - start, h.end - start)
        for h in hits
        if start <= h.start and h.end <= end
    ]
    return _apply(text[start:end], inside)[0].strip()


def _jev_request(
    text: str, hits: list[_Hit]
) -> tuple[list[tuple[int, int]], dict[str, str], dict[str, dict[str, object]]]:
    spans = _chunks(text)
    state = {f"s{i}": _masked(text, s, e, hits) for i, (s, e) in enumerate(spans)}
    questions: dict[str, dict[str, object]] = {
        key: {
            "type": "noul",
            "instructions": (
                f"The text in {key} still contains personal information that identifies a real "
                "person: a full name, home address, date of birth, account or ID number, or "
                "contact details. Bracketed placeholders like [EMAIL] are already redacted and "
                "do not count."
            ),
        }
        for key in state
    }
    return spans, state, questions


def _strict_hits(spans: list[tuple[int, int]], result: JevResult, hits: list[_Hit]) -> list[_Hit]:
    flagged = []
    for i, (start, end) in enumerate(spans):
        answer = result.answers.get(f"s{i}")
        if answer is not None and answer.noul is not None and answer.noul > STRICT_THRESHOLD:
            flagged.append(_Hit("custom", start, end))
    # a flagged sentence replaces the regex hits inside it
    kept = [h for h in hits if not any(f.start <= h.start and h.end <= f.end for f in flagged)]
    return kept + flagged


@dataclass(slots=True)
class _BackendCache:
    factory: Callable[[], JevBackend] | None = None
    backend: JevBackend | None = None


_cache = _BackendCache()


def _backend() -> JevBackend:
    """One shared Jev client for redaction, rebuilt if the Guard backend factory changes."""
    from jev_guard import guard as guard_module  # noqa: PLC0415 (import cycle; tests patch it)

    factory = guard_module._backend_factory
    if _cache.factory is not factory or _cache.backend is None:
        _cache.factory = factory
        _cache.backend = factory()
    return _cache.backend


def _check_level(level: str) -> None:
    if level not in ("fast", "strict"):
        raise ValueError(f"level must be 'fast' or 'strict', got {level!r}")


def redact(text: str, level: Level = "strict") -> tuple[str, list[Found]]:
    """Replace personal data in ``text``. Returns the redacted text and what was found."""
    _check_level(level)
    hits = _regex_hits(text)
    if level == "fast" or not text.strip():
        return _apply(text, hits)
    spans, state, questions = _jev_request(text, hits)
    if not spans:
        return _apply(text, hits)
    return _apply(text, _strict_hits(spans, _backend().evaluate(state, questions), hits))


async def aredact(text: str, level: Level = "strict") -> tuple[str, list[Found]]:
    """Async ``redact``."""
    _check_level(level)
    hits = _regex_hits(text)
    if level == "fast" or not text.strip():
        return _apply(text, hits)
    spans, state, questions = _jev_request(text, hits)
    if not spans:
        return _apply(text, hits)
    result = await _backend().aevaluate(state, questions)
    return _apply(text, _strict_hits(spans, result, hits))
