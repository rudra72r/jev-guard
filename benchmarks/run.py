"""Run the benchmark. Free by default; paid detectors only run with --run, under --max-cost.

    python -m benchmarks.run                                  # plan + free regex baseline
    python -m benchmarks.run --run --max-cost 0.50            # everything with a key set
    python -m benchmarks.run --run --detectors jev,regex --datasets golden,jackhhao

Keys: TYPESAFE_API_KEY (jev-guard), ANTHROPIC_API_KEY (LLM judge). LLM Guard needs
`pip install llm-guard` and runs locally. Results are written to benchmarks/results/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks import datasets as ds
from benchmarks.detectors import DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PRICE, Detector, build
from jev_guard import backends

RESULTS = Path(__file__).resolve().parent / "results"
FREE_PREFIXES = ("regex", "LLM Guard")  # no API spend; a free jev detector is detected by cost


@dataclass(slots=True)
class Tally:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    errors: int = 0
    latencies: list[float] = field(default_factory=list)
    cost: float = 0.0

    def add(self, attack: bool, flagged: bool, latency_ms: float, cost: float) -> None:
        if attack and flagged:
            self.tp += 1
        elif attack:
            self.fn += 1
        elif flagged:
            self.fp += 1
        else:
            self.tn += 1
        self.latencies.append(latency_ms)
        self.cost += cost

    @property
    def checked(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    def summary(self) -> dict[str, Any]:
        attacks, benign = self.tp + self.fn, self.fp + self.tn
        precision = self.tp / (self.tp + self.fp) if self.tp + self.fp else None
        recall = self.tp / attacks if attacks else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall
            else None
        )
        lat = sorted(self.latencies)
        return {
            "checked": self.checked,
            "attacks": attacks,
            "benign": benign,
            "detection_rate": recall,
            "false_alarm_rate": self.fp / benign if benign else None,
            "precision": precision,
            "f1": f1,
            "p50_ms": statistics.median(lat) if lat else None,
            "p95_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None,
            "cost_per_1k_usd": self.cost / self.checked * 1000 if self.checked else 0.0,
            "errors": self.errors,
        }


async def run_detector(
    detector: Detector,
    samples: list[ds.Sample],
    *,
    budget: float,
    concurrency: int,
) -> dict[str, Tally]:
    tallies: dict[str, Tally] = {}
    semaphore = asyncio.Semaphore(concurrency)
    spent = {"usd": 0.0}

    async def one(sample: ds.Sample) -> None:
        tally = tallies.setdefault(sample.dataset, Tally())
        async with semaphore:
            estimate = detector.estimate_cost(sample.text)
            if spent["usd"] + estimate > budget:
                return  # over this detector's share of the budget: leave it unchecked
            spent["usd"] += estimate
            try:
                result = await detector.detect(sample.text)
            except Exception:  # count it and keep going; a flaky API mustn't sink the run
                tally.errors += 1
                spent["usd"] -= estimate
                return
            spent["usd"] += result.cost_usd - estimate
            tally.add(sample.attack, result.flagged, result.latency_ms, result.cost_usd)

    await asyncio.gather(*(one(s) for s in samples))
    return tallies


def _fmt(value: Any, pct: bool = False, money: bool = False) -> str:
    if value is None:
        return "–"
    if pct:
        return f"{value:.0%}"
    if money:
        return f"${value:.4f}" if value else "$0"
    return f"{value:.0f}"


def render_markdown(results: dict[str, dict[str, dict[str, Any]]], meta: dict[str, Any]) -> str:
    lines = [
        f"# Prompt-injection benchmark ({meta['date']})",
        "",
        f"Samples: {meta['samples']} from {', '.join(meta['datasets'])}. "
        "An attack counts as detected when the detector flags it (jev-guard: the verdict blocks).",
        "",
    ]
    for dataset in [*meta["datasets"], "all"]:
        lines += [
            f"## {dataset}",
            "",
            "| detector | detection rate | false alarms | precision | F1 | p50 | p95 "
            "| $ / 1k checks | checked |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for detector, per_dataset in results.items():
            s = per_dataset.get(dataset)
            if not s:
                continue
            lines.append(
                f"| {detector} | {_fmt(s['detection_rate'], pct=True)} | "
                f"{_fmt(s['false_alarm_rate'], pct=True)} | {_fmt(s['precision'], pct=True)} | "
                f"{_fmt(s['f1'], pct=True)} | {_fmt(s['p50_ms'])} ms | {_fmt(s['p95_ms'])} ms | "
                f"{_fmt(s['cost_per_1k_usd'], money=True)} | {s['checked']} |"
            )
        lines.append("")
    if meta["skipped"]:
        lines += ["Skipped: " + "; ".join(f"{k} ({v})" for k, v in meta["skipped"].items()), ""]
    lines += [
        "Notes: deepset contains some non-English rows and a few debatable labels; gandalf is "
        "attacks only (no false-alarm rate). LLM judge cost uses the prices passed with "
        "--judge-price-in/out. LLM Guard runs locally, so its cost is compute, not API spend.",
    ]
    return "\n".join(lines) + "\n"


def _merge(tallies: dict[str, Tally]) -> Tally:
    total = Tally()
    for t in tallies.values():
        total.tp += t.tp
        total.fp += t.fp
        total.fn += t.fn
        total.tn += t.tn
        total.errors += t.errors
        total.latencies += t.latencies
        total.cost += t.cost
    return total


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--datasets", default="golden,deepset,jackhhao,gandalf")
    parser.add_argument("--detectors", default="regex,jev,judge,llm-guard")
    parser.add_argument("--limit", type=int, default=None, help="Max samples per dataset.")
    parser.add_argument("--policy", default="general", help="jev-guard policy.")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--judge-price-in", type=float, default=DEFAULT_JUDGE_PRICE[0])
    parser.add_argument("--judge-price-out", type=float, default=DEFAULT_JUDGE_PRICE[1])
    parser.add_argument(
        "--backend",
        default=None,
        help="Backend for the jev detector: jev, local, ollama:MODEL, ... "
        "(see jev_guard.backends).",
    )
    parser.add_argument("--run", action="store_true", help="Call paid APIs (otherwise free only).")
    parser.add_argument("--max-cost", type=float, default=1.00, help="Total USD cap.")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", type=Path, default=RESULTS)
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    names = [d.strip() for d in args.datasets.split(",") if d.strip()]
    unknown = sorted(set(names) - set(ds.DATASETS))
    if unknown:
        print(f"Unknown dataset(s): {unknown}. Choose from {list(ds.DATASETS)}.")
        return 1
    if args.backend:
        backends.set_backend(args.backend)
    samples = ds.load(names, limit=args.limit)
    detectors = build(
        [d.strip() for d in args.detectors.split(",") if d.strip()],
        policy=args.policy,
        judge_model=args.judge_model,
        judge_price=(args.judge_price_in, args.judge_price_out),
    )

    ready: list[Detector] = []
    skipped: dict[str, str] = {}
    for detector in detectors:
        free = detector.name.startswith(FREE_PREFIXES) or detector.estimate_cost("probe") == 0.0
        reason = detector.available() or (None if args.run or free else "needs --run")
        if reason:
            skipped[detector.name] = reason
        else:
            ready.append(detector)

    estimates = {d.name: sum(d.estimate_cost(s.text) for s in samples) for d in ready}
    total = sum(estimates.values())
    print(f"{len(samples)} samples ({', '.join(names)}); estimated total ${total:.4f}")
    for name, cost in estimates.items():
        print(f"  {name}: ${cost:.4f}")
    for name, reason in skipped.items():
        print(f"  skipped {name}: {reason}")
    if total > args.max_cost:
        print(f"Estimated ${total:.4f} is over --max-cost ${args.max_cost:.2f}.")
        print("Use --limit, fewer --datasets, or a higher --max-cost.")
        return 1

    results: dict[str, dict[str, dict[str, Any]]] = {}
    for detector in ready:
        share = args.max_cost * (estimates[detector.name] / total) if total else args.max_cost
        tallies = await run_detector(detector, samples, budget=share, concurrency=args.concurrency)
        results[detector.name] = {name: t.summary() for name, t in tallies.items()}
        results[detector.name]["all"] = _merge(tallies).summary()

    meta = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "samples": len(samples),
        "datasets": names,
        "skipped": skipped,
        "policy": args.policy,
        "judge_model": args.judge_model,
    }
    markdown = render_markdown(results, meta)
    print("\n" + markdown)
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (args.out / f"benchmark-{stamp}.md").write_text(markdown, encoding="utf-8")
    (args.out / f"benchmark-{stamp}.json").write_text(
        json.dumps({"meta": meta, "results": results}, indent=2), encoding="utf-8"
    )
    print(f"Saved to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
