"""The golden-dataset eval behind `jev-guard eval`.

A dataset is a directory with ``manifest.json`` plus JSONL files of labelled samples::

    {"id": "jb-001", "category": "direct_override", "source": "OWASP LLM01",
     "input": "...", "output": "... (optional)", "context": ["..."] (optional),
     "label": {"is_prompt_injection": true}}

``label`` maps question names to whether that question *should* fire. Labels for questions
the chosen policy doesn't ask are reported as skipped, not counted as misses. The manifest's
``minimums`` set the precision / recall / F1 floor per question (``flagged`` = any action
other than allow); falling below one makes the eval fail.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jev_guard.errors import ConfigurationError
from jev_guard.eval.logs import LogRecord
from jev_guard.eval.metrics import Counts, Metrics, add_example, worst_action
from jev_guard.eval.scan import DEFAULT_CONCURRENCY, ScanResult, estimate_scan_cost, run_scan
from jev_guard.policies.base import Policy

DEFAULT_DATASET_DIR = Path(__file__).resolve().parent / "datasets"
DEFAULT_EVAL_MAX_COST_USD = 0.10
SNIPPET_CHARS = 160


class GoldenSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    category: str = "uncategorized"
    source: str = ""
    input: str
    output: str | None = None
    context: list[str] | None = None
    label: dict[str, bool] = Field(min_length=1)


class Minimum(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    precision: float | None = Field(default=None, ge=0, le=1)
    recall: float | None = Field(default=None, ge=0, le=1)
    f1: float | None = Field(default=None, ge=0, le=1)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str = "0.0.0"
    policy: str = "general"
    files: list[str] = Field(min_length=1)
    provisional: bool = False
    notes: str = ""
    minimums: dict[str, Minimum] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GoldenSet:
    source: str
    manifest: Manifest | None
    samples: list[GoldenSample]


@dataclass(slots=True)
class EvalResult:
    golden: GoldenSet
    policy: Policy
    scan: ScanResult
    records: list[LogRecord]
    metrics: Metrics
    failures: list[str] = field(default_factory=list)
    skipped_questions: list[str] = field(default_factory=list)
    per_category: dict[str, dict[str, Any]] = field(default_factory=dict)
    mistakes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


# --- loading ------------------------------------------------------------------------------


def load_golden(path: str | Path | None = None) -> GoldenSet:
    """Load a dataset directory (with manifest.json) or a single JSONL file (no minimums)."""
    target = Path(path) if path is not None else DEFAULT_DATASET_DIR
    if not target.exists():
        raise ConfigurationError(
            f"Dataset {target} does not exist.",
            hint="Omit --dataset to use the builtin golden set, or pass a .jsonl file or folder.",
        )
    manifest: Manifest | None = None
    if target.is_dir():
        manifest_path = target / "manifest.json"
        if manifest_path.exists():
            manifest = _load_manifest(manifest_path)
            files = [target / name for name in manifest.files]
        else:
            files = sorted(target.glob("*.jsonl"))
        if not files:
            raise ConfigurationError(f"No .jsonl files in {target}.")
    else:
        files = [target]

    samples: list[GoldenSample] = []
    seen: dict[str, str] = {}
    for file in files:
        for line_no, sample in _read_samples(file):
            where = f"{file.name}:{line_no}"
            if sample.id in seen:
                raise ConfigurationError(
                    f"Duplicate sample id {sample.id!r} at {where} "
                    f"(first seen at {seen[sample.id]}).",
                    hint="Give every sample a unique id.",
                )
            seen[sample.id] = where
            samples.append(sample)
    return GoldenSet(source=_describe(target, manifest), manifest=manifest, samples=samples)


def _describe(target: Path, manifest: Manifest | None) -> str:
    """What the report calls this dataset.

    Reports get committed and pasted into issues, so the bundled set shouldn't print as
    whatever absolute path it happens to live at on the machine that ran it.
    """
    if target == DEFAULT_DATASET_DIR:
        return manifest.name if manifest else "bundled golden dataset"
    return str(target)


def _load_manifest(path: Path) -> Manifest:
    try:
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as err:
        raise ConfigurationError(
            f"Invalid manifest {path}: {err}", hint="Compare it with the builtin manifest.json."
        ) from err


def _read_samples(path: Path) -> list[tuple[int, GoldenSample]]:
    if not path.exists():
        raise ConfigurationError(f"Dataset file {path} listed in the manifest does not exist.")
    samples = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            samples.append((line_no, GoldenSample.model_validate(json.loads(line))))
        except (json.JSONDecodeError, ValidationError) as err:
            raise ConfigurationError(
                f"{path.name} line {line_no}: invalid sample ({err})",
                hint='Each line needs "id", "input", and a "label" mapping question -> bool.',
            ) from err
    return samples


# --- running ------------------------------------------------------------------------------


def _asked(policy: Policy) -> set[str]:
    return set(policy.input) | set(policy.output)


def to_records(policy: Policy, golden: GoldenSet) -> tuple[list[LogRecord], list[str]]:
    """Samples as scan records, with labels trimmed to questions this policy asks."""
    asked = _asked(policy)
    skipped: set[str] = set()
    records = []
    for index, sample in enumerate(golden.samples, 1):
        label = {q: v for q, v in sample.label.items() if q in asked}
        skipped |= set(sample.label) - asked
        records.append(
            LogRecord(
                line=index,
                input=sample.input,
                output=sample.output,
                context=sample.context,
                label=label or None,
            )
        )
    return records, sorted(skipped)


def estimate_eval_cost(policy: Policy, golden: GoldenSet) -> float:
    return estimate_scan_cost(policy, to_records(policy, golden)[0])


async def run_golden(
    policy: Policy,
    golden: GoldenSet,
    *,
    max_cost_usd: float = DEFAULT_EVAL_MAX_COST_USD,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress: Callable[[], None] | None = None,
) -> EvalResult:
    records, skipped = to_records(policy, golden)
    scan = await run_scan(
        policy, records, max_cost_usd=max_cost_usd, concurrency=concurrency, on_progress=on_progress
    )
    return score(policy, golden, scan, records, skipped)


def score(
    policy: Policy,
    golden: GoldenSet,
    scan: ScanResult,
    records: list[LogRecord],
    skipped: list[str],
) -> EvalResult:
    metrics = Metrics()
    categories: dict[str, Counts] = defaultdict(Counts)
    mistakes: list[dict[str, Any]] = []
    unchecked = 0

    for sample, item in zip(golden.samples, scan.items, strict=True):
        record = item.record
        if item.skipped or item.error or not any(item.verdicts):
            unchecked += 1
            continue
        label = record.label
        if not isinstance(label, dict):  # golden labels are always per-question mappings
            continue
        add_example(metrics, policy, label, item.verdicts)
        expected = any(label.values())
        got = worst_action(item.verdicts)
        categories[sample.category].add(got != "allow", expected)
        if (got != "allow") != expected:
            reasons = [r for v in item.verdicts if v is not None for r in v.reasons]
            mistakes.append(
                {
                    "id": sample.id,
                    "category": sample.category,
                    "kind": "missed" if expected else "false alarm",
                    "expected": "flag" if expected else "allow",
                    "got": got,
                    "reasons": reasons,
                    "text": sample.input[:SNIPPET_CHARS],
                }
            )

    result = EvalResult(
        golden=golden,
        policy=policy,
        scan=scan,
        records=records,
        metrics=metrics,
        skipped_questions=skipped,
        per_category={
            name: {
                "samples": c.tp + c.fp + c.fn + c.tn,
                "correct": c.tp + c.tn,
                "accuracy": round((c.tp + c.tn) / max(1, c.tp + c.fp + c.fn + c.tn), 4),
            }
            for name, c in sorted(categories.items())
        },
        mistakes=sorted(mistakes, key=lambda m: (m["kind"], m["id"])),
    )
    if unchecked:
        result.failures.append(
            f"{unchecked} samples were not checked (cost cap or API errors); metrics are incomplete"
        )
    if metrics.labelled == 0 and len(golden.samples) > unchecked:
        result.failures.append(
            f"no sample is labelled for a question policy {policy.name!r} asks, so nothing was "
            "evaluated; label samples with this policy's question names"
        )
        return result
    result.failures.extend(check_minimums(result))
    return result


def check_minimums(result: EvalResult) -> list[str]:
    manifest = result.golden.manifest
    if manifest is None:
        return []
    failures = []
    for question, floor in manifest.minimums.items():
        counts = (
            result.metrics.flagged
            if question == "flagged"
            else result.metrics.per_question.get(question)
        )
        if counts is None:
            continue  # not asked by this policy, or no labelled samples
        for metric in ("precision", "recall", "f1"):
            minimum = getattr(floor, metric)
            actual = getattr(counts, metric)
            if minimum is not None and actual < minimum:
                failures.append(f"{question} {metric} {actual:.2f} < minimum {minimum:.2f}")
    return failures


def eval_section(result: EvalResult) -> dict[str, Any]:
    """The eval-specific part of a report (added to the scan report under ``"eval"``)."""
    manifest = result.golden.manifest
    minimums = (
        {q: m.model_dump(exclude_none=True) for q, m in manifest.minimums.items()}
        if manifest
        else {}
    )
    return {
        "dataset": manifest.name if manifest else result.golden.source,
        "dataset_version": manifest.version if manifest else None,
        "provisional": bool(manifest and manifest.provisional),
        "samples": len(result.golden.samples),
        "passed": result.passed,
        "failures": result.failures,
        "minimums": minimums,
        "skipped_questions": result.skipped_questions,
        "per_category": result.per_category,
        "mistakes": result.mistakes,
    }
