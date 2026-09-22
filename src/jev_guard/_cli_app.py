"""Typer app behind the ``jev-guard`` command. Requires ``pip install jev-guard[cli]``."""

from __future__ import annotations

import asyncio
import functools
import json
import os
import sys
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, ParamSpec, TypeVar

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn
from rich.table import Column, Table

from jev_guard import __version__
from jev_guard.agents import ToolGuard
from jev_guard.errors import ConfigurationError, JevGuardError, PolicyError
from jev_guard.eval.golden import (
    DEFAULT_EVAL_MAX_COST_USD,
    estimate_eval_cost,
    load_golden,
    run_golden,
)
from jev_guard.eval.logs import read_log_file
from jev_guard.eval.report import RENDERERS, build_eval_report, build_report, metric_rows
from jev_guard.eval.scan import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_COST_USD,
    estimate_scan_cost,
    run_scan,
)
from jev_guard.guard import Guard, _resolve_policy
from jev_guard.integrations.claude_code import BLOCK_EXIT, handle_event
from jev_guard.policies import BUILTIN_POLICIES, Policy
from jev_guard.policies.loader import dump_policy, validate_policy_file
from jev_guard.types import Verdict

EXIT_USER_ERROR = 1
EXIT_FAIL_ON = 3
EXIT_BELOW_MINIMUM = 4
WARNINGS_SHOWN = 5

app = typer.Typer(
    name="jev-guard",
    help="Fast, cheap guardrails for LLM apps, powered by TypeSafe's Jev model.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    add_completion=False,
)
policy_app = typer.Typer(help="List, show, and validate policies.", no_args_is_help=True)
app.add_typer(policy_app, name="policy")
hook_app = typer.Typer(help="Run jev-guard as a hook inside other tools.", no_args_is_help=True)
app.add_typer(hook_app, name="hook")

# soft_wrap: never insert hard line breaks into messages. Rich otherwise wraps at 80 columns
# when not writing to a terminal (CI, pipes, log files), splitting paths and error lines.
out = Console(soft_wrap=True)
err = Console(stderr=True, soft_wrap=True)
_state = {"debug": False}

P = ParamSpec("P")
R = TypeVar("R")


class ReportFormat(str, Enum):
    html = "html"
    json = "json"
    md = "md"


class FailOn(str, Enum):
    never = "never"
    review = "review"
    block = "block"


def friendly(func: Callable[P, R]) -> Callable[P, R]:
    """Turn jev-guard errors into one message plus a next step (tracebacks only with --debug)."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except JevGuardError as error:
            if _state["debug"]:
                raise
            err.print(f"[bold red]error:[/] {error.args[0]}", highlight=False)
            err.print(f"  [bold]next step:[/] {error.hint}", highlight=False)
            raise typer.Exit(EXIT_USER_ERROR) from None

    return wrapper


def _version(value: bool) -> None:
    if value:
        out.print(f"jev-guard {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    debug: Annotated[bool, typer.Option("--debug", help="Show full tracebacks.")] = False,
    version: Annotated[  # noqa: ARG001 (handled by the eager callback)
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show the version."),
    ] = False,
) -> None:
    _state["debug"] = debug


# --- check --------------------------------------------------------------------------------


def _print_verdict(verdict: Verdict) -> None:
    color = {"allow": "green", "review": "yellow", "block": "red"}[verdict.action]
    out.print(
        f"[bold]{verdict.stage}[/]: [bold {color}]{verdict.action.upper()}[/]  "
        f"confidence {verdict.confidence:.2f} · {verdict.latency_ms:.0f} ms · "
        f"${verdict.estimated_cost_usd:.6f}",
        highlight=False,
    )
    for reason in verdict.reasons:
        out.print(f"  - {reason}", highlight=False, markup=False)
    if verdict.suggested_response:
        out.print(f"  suggested reply: {verdict.suggested_response}", highlight=False, markup=False)


@app.command()
@friendly
def check(
    message: Annotated[str, typer.Argument(help="The user message to check.")],
    policy: Annotated[
        str, typer.Option("--policy", "-p", help="Builtin name or YAML path.")
    ] = "general",
    output: Annotated[
        str | None, typer.Option("--output", "-o", help="Also check this LLM response.")
    ] = None,
    context: Annotated[
        list[str] | None,
        typer.Option("--context", "-c", help="Retrieved document for RAG checks (repeatable)."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print verdicts as JSON.")] = False,
    fail_on: Annotated[
        FailOn, typer.Option(help="Exit with code 3 if any verdict reaches this action.")
    ] = FailOn.never,
) -> None:
    """One-shot check of a message (and optionally a response). Costs one or two Jev calls."""
    with Guard(policy=policy) as guard:
        verdicts = [guard.check_input(message)]
        if output is not None:
            verdicts.append(guard.check_output(message, output, context=context))
    if as_json:
        typer.echo(json.dumps([v.model_dump() for v in verdicts], indent=2))
    else:
        for verdict in verdicts:
            _print_verdict(verdict)
    rank = {"allow": 0, "review": 1, "block": 2}
    if fail_on is not FailOn.never and any(rank[v.action] >= rank[fail_on.value] for v in verdicts):
        raise typer.Exit(EXIT_FAIL_ON)


# --- scan ---------------------------------------------------------------------------------


def _require_budget_and_key(estimate: float, max_cost: float) -> None:
    """Refuse before any Jev call if it would cost too much or can't authenticate."""
    if estimate > max_cost:
        raise ConfigurationError(
            f"Estimated cost ${estimate:.4f} is over --max-cost ${max_cost:.4f}.",
            hint=f"Re-run with --max-cost {max(estimate * 1.2, 0.01):.2f}, or use less data.",
        )
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        raise ConfigurationError(
            "TYPESAFE_API_KEY is not set, so jev-guard can't reach Jev.",
            hint="Set TYPESAFE_API_KEY (https://console.typesafe.ai/keys), or use --dry-run.",
        )


_SUFFIX_FORMATS = {
    ".html": ReportFormat.html,
    ".htm": ReportFormat.html,
    ".json": ReportFormat.json,
    ".md": ReportFormat.md,
    ".markdown": ReportFormat.md,
}


def _format_for(path: Path | None, explicit: ReportFormat | None) -> ReportFormat | None:
    """--format wins; else --out's extension (HTML if unknown); None means terminal only."""
    if explicit is not None:
        return explicit
    if path is None:
        return None
    return _SUFFIX_FORMATS.get(path.suffix.lower(), ReportFormat.html)


@app.command()
@friendly
def scan(
    logs: Annotated[
        Path,
        typer.Argument(
            exists=True, dir_okay=False, readable=True, help="JSONL log (native/Langfuse/Arize)."
        ),
    ],
    policy: Annotated[
        str, typer.Option("--policy", "-p", help="Builtin name or YAML path.")
    ] = "general",
    out_path: Annotated[
        Path | None, typer.Option("--out", help="Write the full report here, e.g. report.html.")
    ] = None,
    report_format: Annotated[
        ReportFormat | None,
        typer.Option("--format", help="html, json, or md. Default: from --out's extension."),
    ] = None,
    max_cost: Annotated[
        float, typer.Option(help="Refuse to spend more than this many USD.", min=0)
    ] = DEFAULT_MAX_COST_USD,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Estimate the cost and exit. No Jev calls.")
    ] = False,
    concurrency: Annotated[
        int, typer.Option(help="Parallel Jev calls.", min=1, max=16)
    ] = DEFAULT_CONCURRENCY,
) -> None:
    """Check every record in a log file and report what would have been flagged."""
    log = read_log_file(logs)
    for warning in log.warnings[:WARNINGS_SHOWN]:
        err.print(f"[yellow]warning:[/] {warning}", highlight=False)
    if len(log.warnings) > WARNINGS_SHOWN:
        hidden = len(log.warnings) - WARNINGS_SHOWN
        err.print(f"[yellow]warning:[/] ...and {hidden} more in the report", highlight=False)
    if not log.records:
        raise ConfigurationError(
            f"No usable records in {logs}.",
            hint='Each line should be a JSON object like {"input": "...", "output": "..."}.',
        )

    resolved = _resolve_policy(policy)
    estimate = estimate_scan_cost(resolved, log.records)
    checks = sum(1 for r in log.records if r.input.strip()) + sum(
        1 for r in log.records if r.output and r.output.strip()
    )
    out.print(
        f"{len(log.records)} records ({log.format} format), {checks} checks with policy "
        f"[bold]{resolved.name}[/]: estimated [bold]${estimate:.4f}[/]",
        highlight=False,
    )
    if dry_run:
        out.print("Dry run: nothing was sent to Jev.")
        return
    _require_budget_and_key(estimate, max_cost)

    with Progress(
        TextColumn("scanning"), BarColumn(), MofNCompleteColumn(), console=err, transient=True
    ) as progress:
        task = progress.add_task("scan", total=len(log.records))
        result = asyncio.run(
            run_scan(
                resolved,
                log.records,
                max_cost_usd=max_cost,
                concurrency=concurrency,
                on_progress=lambda: progress.advance(task),
            )
        )
    result.warnings.extend(log.warnings)
    report = build_report(result, source=str(logs), log_format=log.format)
    _print_scan_summary(report)

    fmt = _format_for(out_path, report_format)
    if fmt is None:
        out.print("Add --out report.html for the full report.", style="dim")
        return
    rendered = RENDERERS[fmt.value](report)
    if out_path is None:
        typer.echo(rendered)
    else:
        out_path.write_text(rendered, encoding="utf-8")
        out.print(f"Report written to [bold]{out_path}[/]", highlight=False)


def _print_scan_summary(report: dict[str, Any]) -> None:
    s = report["summary"]
    table = Table("stage", "allow", "review", "block", title="Actions")
    for stage, counts in report["actions"].items():
        table.add_row(
            stage,
            str(counts["allow"]),
            f"[yellow]{counts['review']}[/]",
            f"[red]{counts['block']}[/]",
        )
    out.print(table)
    out.print(
        f"{s['flagged']} of {s['records']} records flagged · {s['checks']} checks · "
        f"${s['cost_usd']:.4f} · avg {s['avg_latency_ms']:.0f} ms",
        highlight=False,
    )
    if s["budget_exhausted"]:
        err.print(
            f"[yellow]Stopped at the cost cap:[/] {s['skipped_budget']} records not checked. "
            "Raise --max-cost to scan them.",
            highlight=False,
        )
    if s["errors"]:
        err.print(f"[red]{s['errors']} records failed[/]; see the report's Errors section.")


# --- eval ---------------------------------------------------------------------------------


@app.command("eval")
@friendly
def eval_command(
    policy: Annotated[
        str | None,
        typer.Option("--policy", "-p", help="Builtin name or YAML path. Default: the manifest's."),
    ] = None,
    dataset: Annotated[
        Path | None,
        typer.Option(help="Dataset folder (with manifest.json) or .jsonl file. Default: builtin."),
    ] = None,
    out_path: Annotated[
        Path | None, typer.Option("--out", help="Write the full report, e.g. eval.html.")
    ] = None,
    report_format: Annotated[
        ReportFormat | None, typer.Option("--format", help="html, json, or md.")
    ] = None,
    max_cost: Annotated[
        float, typer.Option(help="Refuse to spend more than this many USD.", min=0)
    ] = DEFAULT_EVAL_MAX_COST_USD,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Estimate the cost and exit. No Jev calls.")
    ] = False,
    concurrency: Annotated[
        int, typer.Option(help="Parallel Jev calls.", min=1, max=16)
    ] = DEFAULT_CONCURRENCY,
) -> None:
    """Score a policy on the labelled golden dataset. Exits 4 if a minimum isn't met."""
    golden = load_golden(dataset)
    resolved = _resolve_policy(policy or (golden.manifest.policy if golden.manifest else None))
    estimate = estimate_eval_cost(resolved, golden)
    out.print(
        f"{len(golden.samples)} samples from {golden.source} with policy "
        f"[bold]{resolved.name}[/]: estimated [bold]${estimate:.4f}[/]",
        highlight=False,
    )
    if dry_run:
        out.print("Dry run: nothing was sent to Jev.")
        return
    _require_budget_and_key(estimate, max_cost)

    with Progress(
        TextColumn("evaluating"), BarColumn(), MofNCompleteColumn(), console=err, transient=True
    ) as progress:
        task = progress.add_task("eval", total=len(golden.samples))
        result = asyncio.run(
            run_golden(
                resolved,
                golden,
                max_cost_usd=max_cost,
                concurrency=concurrency,
                on_progress=lambda: progress.advance(task),
            )
        )
    report = build_eval_report(result)
    _print_eval_summary(report)
    fmt = _format_for(out_path, report_format)
    if fmt is not None:
        rendered = RENDERERS[fmt.value](report)
        if out_path is None:
            typer.echo(rendered)
        else:
            out_path.write_text(rendered, encoding="utf-8")
            out.print(f"Report written to [bold]{out_path}[/]", highlight=False)
    if not result.passed:
        raise typer.Exit(EXIT_BELOW_MINIMUM)


def _print_eval_summary(report: dict[str, Any]) -> None:
    ev = report["eval"]
    if report["metrics"]:
        table = Table("question", "precision", "recall", "F1", "support", "minimum")
        for row in metric_rows(report["metrics"], ev["minimums"]):
            table.add_row(
                row["name"],
                f"{row['precision']:.2f}",
                f"{row['recall']:.2f}",
                f"{row['f1']:.2f}",
                str(row["support"]),
                row["minimum"],
            )
        out.print(table)
    s = report["summary"]
    out.print(
        f"{s['checks']} checks · ${s['cost_usd']:.4f} · avg {s['avg_latency_ms']:.0f} ms · "
        f"{len(ev['mistakes'])} mistakes",
        highlight=False,
    )
    if ev["passed"]:
        out.print("[bold green]PASSED[/]: all minimums met.")
    else:
        err.print("[bold red]FAILED[/]:")
        for failure in ev["failures"]:
            err.print(f"  - {failure}", highlight=False, markup=False)
    if ev["provisional"]:
        out.print("Minimums are provisional until measured against real Jev.", style="dim")


# --- hook ---------------------------------------------------------------------------------


@hook_app.command("claude-code")
def hook_claude_code(
    policy: Annotated[
        str, typer.Option("--policy", "-p", help="Tool policy: builtin name or YAML path.")
    ] = "agent_tools",
    fail_closed: Annotated[
        bool, typer.Option("--fail-closed", help="Block when Jev can't be reached.")
    ] = False,
) -> None:
    """Claude Code PreToolUse / PostToolUse hook. Reads the hook event JSON on stdin."""
    try:
        tool_guard = ToolGuard(policy)
    except JevGuardError as error:  # a bad policy must be visible, and must not fail open
        sys.stderr.write(f"jev-guard: {error.args[0]}\n")
        raise typer.Exit(BLOCK_EXIT) from None
    result = handle_event(sys.stdin.read(), tool_guard, fail_closed=fail_closed)
    if result.stdout:
        sys.stdout.write(result.stdout + "\n")
    if result.stderr:
        sys.stderr.write(result.stderr + "\n")
    raise typer.Exit(result.exit_code)


# --- policy -------------------------------------------------------------------------------


@policy_app.command("list")
@friendly
def policy_list() -> None:
    """List the builtin policies."""
    table = Table("description", "input Qs", "output Qs")
    # Names must stay readable in narrow terminals; descriptions wrap instead.
    table.columns.insert(
        0, Column("name", no_wrap=True, min_width=len(max(BUILTIN_POLICIES, key=len)))
    )
    for name in BUILTIN_POLICIES:
        p = Policy.from_builtin(name)
        table.add_row(name, p.description, str(len(p.input)), str(len(p.output)))
    out.print(table)
    out.print("Show one with: jev-guard policy show NAME", style="dim")


@policy_app.command("show")
@friendly
def policy_show(
    name: Annotated[str, typer.Argument(help="Builtin name or YAML path.")],
) -> None:
    """Print a policy as YAML (copy it to make your own)."""
    typer.echo(dump_policy(_resolve_policy(name)), nl=False)


@policy_app.command("validate")
@friendly
def policy_validate(
    path: Annotated[Path, typer.Argument(help="The policy YAML file to check.")],
) -> None:
    """Check a policy file and list every problem with its line number."""
    issues = validate_policy_file(path)
    if issues:
        for issue in issues:
            err.print(f"{path} {issue}", highlight=False, markup=False)
        raise PolicyError(
            f"{len(issues)} problem(s) found in {path}.",
            hint="Fix the lines above and run this command again.",
        )
    policy = Policy.from_yaml(path)
    out.print(
        f"OK: {path} is valid (policy {policy.name!r}, {len(policy.input)} input and "
        f"{len(policy.output)} output questions)",
        highlight=False,
    )
