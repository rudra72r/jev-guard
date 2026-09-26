"""Everything CI checks, plus the things CI can't, in one command.

    python scripts/preflight.py

Run this before tagging a release. It runs the same lint, type, test, docs and packaging
checks as CI, then verifies the things that only matter on a developer machine: that no API
key is about to be committed, and that the built wheel installs and works in a clean
environment. Nothing here makes a network call to Jev, so it never costs anything.

Exit code 0 means the repo is ready to publish; the summary at the end says what to do next.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
GREEN, RED, YELLOW, DIM, BOLD, OFF = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[1m",
    "\033[0m",
)

# An API key in a tracked file is the one mistake that can't be undone by a follow-up commit.
KEY_PATTERN = re.compile(r"(sk-[A-Za-z0-9]{20,}|ts-[A-Za-z0-9]{20,})")
KEY_ALLOWED = {".env.example", "scripts/preflight.py"}


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0


def run(name: str, command: list[str], *, cwd: Path = ROOT) -> Result:
    start = time.perf_counter()
    proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    elapsed = time.perf_counter() - start
    if proc.returncode == 0:
        return Result(name, True, seconds=elapsed)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    return Result(name, False, "\n".join(tail[-25:]), elapsed)


def check_no_secrets() -> Result:
    """No tracked file may contain something shaped like an API key."""
    listing = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    offenders = []
    for name in listing.stdout.split():
        if name in KEY_ALLOWED:
            continue
        path = ROOT / name
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if KEY_PATTERN.search(text):
            offenders.append(name)
    if offenders:
        return Result("no API keys in tracked files", False, "found in: " + ", ".join(offenders))
    return Result("no API keys in tracked files", True)


def check_env_untracked() -> Result:
    """.env holds the developer's real key and must never be tracked."""
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode == 0:
        return Result(".env is not tracked", False, ".env is in git — remove it before pushing")
    return Result(".env is not tracked", True)


def _version() -> str:
    text = (ROOT / "src" / "jev_guard" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__ = "([^"]+)"', text)
    return match.group(1) if match else "0.0.0"


def check_release_tag() -> Result:
    """A release tag must point at what you're about to publish.

    The tag is what the publish workflow builds from, and a PyPI version can never be
    re-uploaded. A tag left behind at an older commit publishes that older code, permanently.
    """
    name = "release tag matches HEAD"
    version = _version()
    tag = f"v{version}"
    exists = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if exists.returncode != 0:
        return Result(name, True, f"{tag} not created yet")
    behind = subprocess.run(
        ["git", "rev-list", f"{tag}..HEAD", "--count"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    missed = behind.stdout.strip()
    if missed not in ("", "0"):
        return Result(
            name,
            False,
            f"{tag} is {missed} commit(s) behind HEAD. Publishing it would ship that older "
            f"code, and PyPI {version} could never be replaced.\n"
            f'  Move it:  git tag -d {tag}    then    git tag -a {tag} -m "jev-guard {version}"',
        )
    return Result(name, True, f"{tag} at HEAD")


def check_clean_install(dist: Path) -> Result:
    """A plain `pip install jev-guard` must import fast and run the CLI's install hint."""
    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        return Result("clean install of the wheel", False, "no wheel in dist/")
    wheel = wheels[-1]
    venv = Path(tempfile.mkdtemp(prefix="jev-guard-preflight-"))
    try:
        subprocess.run([PY, "-m", "venv", str(venv)], check=True, capture_output=True)
        bin_dir = venv / ("Scripts" if sys.platform == "win32" else "bin")
        python = bin_dir / ("python.exe" if sys.platform == "win32" else "python")
        install = subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", str(wheel)],
            capture_output=True,
            text=True,
            check=False,
        )
        if install.returncode != 0:
            return Result("clean install of the wheel", False, install.stderr.strip()[-800:])
        smoke = subprocess.run(
            [
                str(python),
                "-c",
                "import jev_guard, sys;"
                "from jev_guard import Guard, Policy, redact;"
                "assert Policy.from_builtin('general').questions_for('input');"
                "assert 'typesafe_sdk' not in sys.modules, 'SDK imported eagerly';"
                "print(jev_guard.__version__)",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if smoke.returncode != 0:
            return Result("clean install of the wheel", False, smoke.stderr.strip()[-800:])
        return Result("clean install of the wheel", True, f"jev-guard {smoke.stdout.strip()}")
    finally:
        shutil.rmtree(venv, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true", help="skip packaging and clean install")
    args = parser.parse_args()

    print(
        f"{BOLD}jev-guard preflight{OFF}  {DIM}(no network calls to Jev; nothing is billed){OFF}\n"
    )

    results = [
        check_env_untracked(),
        check_no_secrets(),
        check_release_tag(),
        run("ruff check", [PY, "-m", "ruff", "check", "."]),
        run("ruff format --check", [PY, "-m", "ruff", "format", "--check", "."]),
        run("mypy", [PY, "-m", "mypy"]),
        run("pytest", [PY, "-m", "pytest", "-q", "-m", "not integration"]),
        run("mkdocs build --strict", [PY, "-m", "mkdocs", "build", "--strict"]),
    ]

    if not args.fast:
        dist = ROOT / "dist"
        shutil.rmtree(dist, ignore_errors=True)
        build = run("hatch build", [PY, "-m", "hatch", "build"])
        results.append(build)
        if build.ok:
            results.append(
                run(
                    "twine check --strict",
                    [
                        PY,
                        "-m",
                        "twine",
                        "check",
                        "--strict",
                        *[str(p) for p in sorted(dist.iterdir())],
                    ],
                )
            )
            results.append(check_clean_install(dist))

    print()
    failed = [r for r in results if not r.ok]
    for result in results:
        mark = f"{GREEN}PASS{OFF}" if result.ok else f"{RED}FAIL{OFF}"
        timing = f"{DIM}{result.seconds:5.1f}s{OFF}" if result.seconds else "      "
        extra = f"  {DIM}{result.detail}{OFF}" if result.ok and result.detail else ""
        print(f"  {mark}  {result.name:<28} {timing}{extra}")

    if failed:
        for result in failed:
            print(f"\n{RED}{BOLD}{result.name} failed{OFF}\n{result.detail}")
        print(f"\n{RED}Not ready to publish.{OFF} Fix the above and run preflight again.")
        return 1

    tag = f"v{_version()}"
    # One command per line, no `&&`: these get pasted into PowerShell as often as into bash,
    # and PowerShell 5.1 rejects `&&` outright.
    print(f"\n{GREEN}{BOLD}Ready to publish.{OFF} To release {tag}, one line at a time:\n")
    for step, comment in (
        ("git push origin main", "CI must be green"),
        (
            "gh repo edit rudra72r/jev-guard --visibility public"
            " --accept-visibility-change-consequences",
            "gh refuses --visibility without that flag",
        ),
        (f"git push origin {tag}", "fires publish.yml"),
    ):
        # A command longer than the column gets its note on the line above, so the command
        # stays on one unbroken line the user can copy.
        if len(step) > 52:  # noqa: PLR2004
            print(f"  {DIM}# {comment}{OFF}\n  {step}")
        else:
            print(f"  {step:<52}{DIM}# {comment}{OFF}")
    print(f"\n  {DIM}PyPI trusted publishing must be configured first — see notes/LAUNCH.md.{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
