## What and why

## Checklist

- [ ] `ruff check .`, `ruff format --check .`, `mypy`, and `pytest -m "not integration"` pass
- [ ] Tests added or updated
- [ ] Docs updated (and `mkdocs build --strict` passes) if behaviour changed
- [ ] **Policy changes:** YAML twins regenerated (`python scripts/export_policies.py`), policy
      `version` bumped, and `jev-guard eval` before/after pasted below
- [ ] No real personal data, keys, or customer text anywhere (tests, samples, docs)

## Eval before / after (policy changes only)
