# Contributing

Thanks for helping. The most valuable contributions, in order:

1. **Missed attacks and false alarms** from real traffic (with synthetic data). Open an
   issue with the "Missed attack / false alarm" template. Each becomes a golden eval sample.
2. **Better question wording** backed by `jev-guard eval` numbers.
3. **New policies** for use cases the builtins don't cover.
4. Bug fixes, integrations, docs.

## Setup

```bash
git clone https://github.com/rudra72r/jev-guard && cd jev-guard
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev,docs]"
```

## Before every pull request

```bash
python scripts/preflight.py --fast   # lint, types, tests, docs — no API key, nothing billed
```

That runs exactly what CI runs (on Python 3.10–3.12), or run the pieces yourself:

```bash
ruff check . && ruff format --check .
mypy
pytest -m "not integration"          # no API key needed, never calls Jev
mkdocs build --strict                # if you touched docs/
```

Before a release, drop `--fast` — the full run also builds the package and installs the
wheel into a throwaway virtualenv to check that a plain `pip install jev-guard` works.

## Tests never spend money

Unit tests run against `FakeJevClient` (see `tests/conftest.py`). Live tests in
`tests/integration/` only run with both `TYPESAFE_API_KEY` and `JEV_GUARD_ALLOW_LIVE=1`, and
they stop at a 200k-token budget. CI has no key, on purpose. Don't add a secret to CI.

## Changing a builtin policy

Policies are what users trust, so changes need evidence:

1. Edit the policy in `src/jev_guard/policies/`.
2. Run `python scripts/export_policies.py` to regenerate the YAML twins in `policies/`
   (a test fails if they drift).
3. Run `jev-guard eval --policy NAME` before and after, and paste both summaries in the pull
   request. Changes that lower precision or recall need a reason.
4. Bump the policy's `version`: patch for wording, minor for thresholds, major for added or
   removed questions.

## Adding golden samples

Edit `scripts/build_golden_dataset.py` and run it. Every sample must:

- be written by you (no copying from other datasets without a compatible licence)
- use only reserved values for personal data (`example.com`, 555-01xx, test card numbers,
  SSNs starting 000/666/9xx). `tests/unit/test_golden_dataset.py` enforces this.

## Style

- Match the surrounding code. mypy strict on `src/`, docstrings on public functions.
- Error messages say what happened and what to do next (`JevGuardError(message, hint=...)`).
- The public API (`Guard`, `Policy`, `Verdict`) is frozen for 0.x.
  Add new capabilities in new modules rather than new `Guard` methods.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/).

## Code of conduct

Be kind and assume good faith. We follow the
[Contributor Covenant 2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).
Report conduct issues privately to the maintainer through GitHub.
