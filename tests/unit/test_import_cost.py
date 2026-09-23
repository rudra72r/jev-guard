"""``import jev_guard`` stays cheap.

A guardrail runs on someone's hot path, and the Claude Code hook starts a fresh process on
*every* tool call, so import time is a feature. The HTTP stacks are the expensive part
(~0.6s for the TypeSafe SDK, ~0.1s for httpx2), and none of them is needed until a backend
actually makes a request. These tests fail if an eager import creeps back in.
"""

from __future__ import annotations

import subprocess
import sys

# Imported only when the backend that needs them makes its first call.
DEFERRED = ("typesafe_sdk", "httpx2", "transformers", "torch", "typer", "rich")


def loaded_modules(code: str) -> set[str]:
    """Top-level module names in sys.modules after running ``code`` in a fresh process."""
    report = "print(' '.join(sorted({m.split('.')[0] for m in sys.modules})))"
    script = f"import sys\n{code}\n{report}"
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    return set(out.stdout.split())


def test_importing_the_library_does_not_load_http_stacks() -> None:
    modules = loaded_modules("import jev_guard")
    assert not modules & set(DEFERRED), (
        f"import jev_guard eagerly loaded {sorted(modules & set(DEFERRED))}; "
        "import it lazily (see JevClient._sdk / judge._httpx)"
    )


def test_public_api_does_not_load_http_stacks() -> None:
    """The names most people import, and the backend registry the CLI touches."""
    modules = loaded_modules(
        "from jev_guard import Guard, redact, backends\nbackends.from_spec('local')"
    )
    assert not modules & {"typesafe_sdk", "httpx2"}


def test_the_sdk_still_loads_when_jev_is_actually_used() -> None:
    """Laziness must not turn a missing dependency into a silent no-op."""
    modules = loaded_modules(
        "from jev_guard.client import _sdk\nassert _sdk().TypeSafeClient is not None"
    )
    assert "typesafe_sdk" in modules
