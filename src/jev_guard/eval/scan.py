"""Batch-check a log file: the engine behind `jev-guard scan`.

Every record costs up to two Jev calls (input and output). Before any call, the cost is
estimated locally; `run_scan` never spends more than ``max_cost_usd``. Once the next check
would cross the cap, the remaining records are skipped and reported as such.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from jev_guard.cost import estimate_cost_usd, estimate_tokens
from jev_guard.errors import ConfigurationError, JevAPIError, JevAuthenticationError
from jev_guard.eval.logs import LogRecord
from jev_guard.guard import Guard
from jev_guard.guards.input_guard import input_state
from jev_guard.guards.output_guard import output_state
from jev_guard.policies.base import Policy
from jev_guard.types import GuardStage, Verdict

# Jev allows 1,200 requests/min (20/s). At ~200 ms per call, 4 in flight is ~20/s.
DEFAULT_CONCURRENCY = 4
DEFAULT_MAX_COST_USD = 1.00


@dataclass(slots=True)
class ScanItem:
    record: LogRecord
    input_verdict: Verdict | None = None
    output_verdict: Verdict | None = None
    error: str | None = None
    skipped: bool = False

    @property
    def verdicts(self) -> list[Verdict | None]:
        return [self.input_verdict, self.output_verdict]


@dataclass(slots=True)
class ScanResult:
    policy: Policy
    items: list[ScanItem]
    duration_s: float = 0.0
    budget_exhausted: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return sum(v.estimated_cost_usd for i in self.items for v in i.verdicts if v)


def _estimate_check(policy: Policy, stage: GuardStage, state: dict[str, object]) -> float:
    questions = {n: s.to_wire() for n, s in policy.questions_for(stage).items()}
    if not questions:
        return 0.0
    return estimate_cost_usd(estimate_tokens({"state": state, "questions": questions}))


def _estimate_record(policy: Policy, r: LogRecord) -> float:
    total = 0.0
    if r.input.strip():
        total += _estimate_check(policy, "input", input_state(r.input))
    if r.output and r.output.strip():
        out_policy = policy if r.context is not None else policy.without_context()
        total += _estimate_check(out_policy, "output", output_state(r.input, r.output, r.context))
    return total


def estimate_scan_cost(policy: Policy, records: list[LogRecord]) -> float:
    """Local estimate of what scanning these records would cost; no network calls."""
    return sum(_estimate_record(policy, r) for r in records)


async def run_scan(
    policy: Policy,
    records: list[LogRecord],
    *,
    max_cost_usd: float = DEFAULT_MAX_COST_USD,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress: Callable[[], None] | None = None,
) -> ScanResult:
    guard = Guard(policy=policy)
    items = [ScanItem(record=r) for r in records]
    semaphore = asyncio.Semaphore(max(1, concurrency))
    budget = {"committed": 0.0, "exhausted": False}
    fatal: list[BaseException] = []

    def reserve(estimate: float) -> bool:
        if budget["committed"] + estimate > max_cost_usd:
            budget["exhausted"] = True
            return False
        budget["committed"] += estimate
        return True

    async def process(item: ScanItem) -> None:
        async with semaphore:
            if fatal:
                item.skipped = True
                return
            r = item.record
            estimate = _estimate_record(policy, r)
            if not reserve(estimate):
                item.skipped = True
                return
            try:
                item.input_verdict = await guard.acheck_input(r.input)
                if r.output:
                    item.output_verdict = await guard.acheck_output(r.input, r.output, r.context)
            except (JevAuthenticationError, ConfigurationError) as err:
                fatal.append(err)  # every later call would fail the same way
                item.error = str(err.args[0])
            except JevAPIError as err:
                item.error = str(err.args[0])
            finally:
                # swap the estimate for what the calls really cost
                actual = sum(v.estimated_cost_usd for v in item.verdicts if v is not None)
                budget["committed"] += actual - estimate
                if on_progress is not None:
                    on_progress()

    start = time.perf_counter()
    await asyncio.gather(*(process(item) for item in items))
    if fatal:
        raise fatal[0]
    return ScanResult(
        policy=policy,
        items=items,
        duration_s=time.perf_counter() - start,
        budget_exhausted=bool(budget["exhausted"]),
    )
