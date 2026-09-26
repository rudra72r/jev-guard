"""How jev-guard tells someone to install one of its extras.

The package isn't on PyPI, so `pip install "jev-guard[local]"` would fail for anyone who
followed it. Every user-facing hint is built here instead of hard-coded, so publishing to
PyPI later is one line: set ``ON_PYPI = True``.
"""

from __future__ import annotations

REPO = "git+https://github.com/rudra72r/jev-guard"
ON_PYPI = False


def install(extra: str | None = None) -> str:
    """The exact command to run, for a copy-paste that works today."""
    name = f"jev-guard[{extra}]" if extra else "jev-guard"
    if ON_PYPI:
        return f'pip install "{name}"' if extra else "pip install jev-guard"
    return f'pip install "{name} @ {REPO}"' if extra else f"pip install {REPO}"
