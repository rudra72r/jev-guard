"""The ``jev-guard`` command. The Typer app lives in ``_cli_app`` so that a plain
``pip install jev-guard`` (without the ``[cli]`` extra) still gives a helpful message
instead of an ImportError traceback.
"""

from __future__ import annotations

import sys

_CLI_DEPS = {"typer", "click", "rich", "jinja2"}


def main() -> None:
    try:
        from jev_guard._cli_app import app  # noqa: PLC0415 (optional dependencies)
    except ModuleNotFoundError as err:
        if (err.name or "").split(".")[0] not in _CLI_DEPS:
            raise
        sys.stderr.write(
            f"jev-guard's command line needs extra packages ({err.name} is missing).\n"
            '  next step: pip install "jev-guard[cli]"\n'
        )
        raise SystemExit(1) from None
    app()


if __name__ == "__main__":
    main()
