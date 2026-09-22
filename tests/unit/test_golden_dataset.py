"""Integrity of the builtin golden dataset: shape, labels, drift, and synthetic-only PII."""

from __future__ import annotations

import base64
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from jev_guard import Policy
from jev_guard.eval.golden import DEFAULT_DATASET_DIR, load_golden

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = load_golden()
ALL_TEXT = [
    (s.id, text) for s in GOLDEN.samples for text in (s.input, s.output or "", *(s.context or []))
]


def _generator():
    spec = importlib.util.spec_from_file_location(
        "build_golden", ROOT / "scripts/build_golden_dataset.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sizes_match_spec():
    by_file = Counter(s.id.split("-")[0] for s in GOLDEN.samples)
    assert by_file == {"jb": 50, "cl": 30, "pii": 20}


def test_dataset_files_are_up_to_date_with_generator():
    generator = _generator()
    for name, rows in generator.build().items():
        actual = (DEFAULT_DATASET_DIR / name).read_text(encoding="utf-8")
        assert actual == generator.render(rows), "run `python scripts/build_golden_dataset.py`"


def test_ids_unique_and_every_sample_labelled():
    ids = [s.id for s in GOLDEN.samples]
    assert len(ids) == len(set(ids))
    assert all(s.label for s in GOLDEN.samples)


def test_labels_only_use_general_policy_questions():
    general = Policy.from_builtin("general")
    asked = set(general.input) | set(general.output)
    used = {q for s in GOLDEN.samples for q in s.label}
    assert used <= asked


def test_manifest_minimums_reference_known_questions():
    assert GOLDEN.manifest is not None
    used = {q for s in GOLDEN.samples for q in s.label}
    assert set(GOLDEN.manifest.minimums) <= used | {"flagged"}
    assert GOLDEN.manifest.provisional, "flip only after measuring against real Jev"


def test_clean_set_includes_hard_negatives():
    categories = Counter(s.category for s in GOLDEN.samples if s.id.startswith("cl-"))
    assert categories["hard_negative"] >= 10


def test_jailbreaks_cover_many_attack_patterns():
    categories = {s.category for s in GOLDEN.samples if s.id.startswith("jb-")}
    assert len(categories) >= 8


def test_encoded_samples_decode_to_real_attacks():
    sample = next(s for s in GOLDEN.samples if "base64" in s.input)
    decoded = base64.b64decode(sample.input.rsplit(" ", 1)[-1]).decode()
    assert "Ignore all previous instructions" in decoded


def test_dataset_is_packaged_with_the_library():
    assert DEFAULT_DATASET_DIR.parent.name == "eval"
    assert (DEFAULT_DATASET_DIR / "manifest.json").exists()
    json.loads((DEFAULT_DATASET_DIR / "manifest.json").read_text(encoding="utf-8"))


# --- synthetic PII only --------------------------------------------------------------------

EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
SAFE_EMAIL_DOMAINS = re.compile(r"^(example\.(com|org|net)|[\w-]+\.test)$")
SSN = re.compile(r"\b(\d{3})-(\d{2})-(\d{4})\b")
PHONE = re.compile(r"(?:\(\d{3}\)\s*|\+1\s*|\b\d{3}[-.\s])?\b(\d{3})[-.\s](\d{4})\b")
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
TEST_CARDS = {"4111111111111111", "5555555555554444"}


def real_looking_pii(text: str) -> list[str]:
    """Every value in ``text`` that could be real personal data. Empty means synthetic-only."""
    problems = []
    for domain in EMAIL.findall(text):
        if not SAFE_EMAIL_DOMAINS.match(domain):
            problems.append(f"email domain {domain}")
    for area, group, serial in SSN.findall(text):
        if not (area in {"000", "666"} or area.startswith("9")):
            problems.append(f"SSN {area}-{group}-{serial}")
    without_ssns = SSN.sub(" ", text)
    for exchange, line in PHONE.findall(without_ssns):
        if not (exchange == "555" and line.startswith("01")):
            problems.append(f"phone {exchange}-{line}")
    for match in CARD.findall(without_ssns):
        digits = re.sub(r"\D", "", match)
        if len(digits) >= 13 and digits not in TEST_CARDS:
            problems.append(f"card {match.strip()}")
    return problems


@pytest.mark.parametrize(("sample_id", "text"), ALL_TEXT)
def test_every_sample_uses_only_synthetic_pii(sample_id, text):
    assert real_looking_pii(text) == [], sample_id


@pytest.mark.parametrize(
    "text",
    [
        "email me at jane.doe@gmail.com",
        "SSN 123-45-6789",
        "call (212) 867-5309",
        "call 212-555-1234",
        "card 4242 4242 4242 4241",
    ],
)
def test_detectors_catch_real_looking_values(text):
    """Guards the guard: each detector must fire on a realistic value."""
    assert real_looking_pii(text) != []


@pytest.mark.parametrize(
    "text",
    [
        "mail a@example.com or b@shop.test",
        "SSN 000-12-3456 or 666-45-0921 or 900-55-1212",
        "call (503) 555-0144 or +1 555-0139",
        "card 4111 1111 1111 1111 or 5555555555554444",
        "order 1042, ticket 881, 30 days, D000-1234-5678",
    ],
)
def test_detectors_accept_reserved_values(text):
    assert real_looking_pii(text) == []
