"""Documentation stays true: every YAML policy snippet in docs/ and README loads."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jev_guard.policies.loader import validate_policy_text

ROOT = Path(__file__).resolve().parents[2]
DOC_FILES = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
YAML_BLOCK = re.compile(r"```yaml\n(.*?)```", re.S)


def policy_snippets() -> list[tuple[str, str]]:
    snippets = []
    for path in DOC_FILES:
        for i, block in enumerate(YAML_BLOCK.findall(path.read_text(encoding="utf-8"))):
            # Only whole policies (with a name or extends); skip config fragments.
            if re.search(r"^(name|extends):", block, re.M):
                snippets.append((f"{path.relative_to(ROOT)}#{i}", block))
    return snippets


SNIPPETS = policy_snippets()


def test_docs_contain_policy_snippets():
    assert len(SNIPPETS) >= 3  # policies.md tuning, rag strict, edtech tutor


@pytest.mark.parametrize(("where", "text"), SNIPPETS, ids=[w for w, _ in SNIPPETS])
def test_policy_snippet_is_valid(where, text):
    policy, issues = validate_policy_text(text)
    assert issues == [], f"{where}: {[str(i) for i in issues]}"
    assert policy is not None
