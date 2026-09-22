"""Labelled prompt-injection datasets for the benchmark.

Public sets are read through Hugging Face's dataset-viewer API (no extra dependency) and
cached in ``benchmarks/.cache/``. Licences were checked on 2026-09-22:

- deepset/prompt-injections (Apache-2.0): 116 test rows, label 1 = injection. Includes
  some non-English rows, and a few labels are debatable ("Generate SQL code to access a
  database." is labelled an injection), so read its numbers alongside the others.
- jackhhao/jailbreak-classification (Apache-2.0): 262 test rows, type "jailbreak" or
  "benign". English role-play jailbreaks against benign persona prompts.
- Lakera/gandalf_ignore_instructions (MIT): attacks only, so it measures detection rate,
  not false alarms.
- golden: jev-guard's own 100 samples (attack = labelled prompt injection or malicious
  intent; the PII samples count as benign here).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jev_guard.eval.golden import load_golden

ROWS_API = "https://datasets-server.huggingface.co/rows"
PAGE = 100
CACHE = Path(__file__).resolve().parent / ".cache"


@dataclass(frozen=True, slots=True)
class Sample:
    dataset: str
    id: str
    text: str
    attack: bool


@dataclass(frozen=True, slots=True)
class HFSource:
    repo: str
    split: str
    text_field: str
    is_attack: Callable[[dict[str, Any]], bool]
    config: str = "default"


HF_SOURCES: dict[str, HFSource] = {
    "deepset": HFSource(
        "deepset/prompt-injections", "test", "text", lambda r: int(r["label"]) == 1
    ),
    "jackhhao": HFSource(
        "jackhhao/jailbreak-classification", "test", "prompt", lambda r: r["type"] == "jailbreak"
    ),
    "gandalf": HFSource("Lakera/gandalf_ignore_instructions", "test", "text", lambda _row: True),
}
DATASETS = ("golden", *HF_SOURCES)


def _get_json(url: str, attempts: int = 4) -> Any:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 (fixed https host)
                return json.loads(response.read().decode("utf-8"))
        except Exception as err:  # the viewer API returns transient 5xx under load
            last = err
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Could not fetch {url}: {last}") from last


def _fetch_rows(source: HFSource, fetch: Callable[[str], Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode(
            {
                "dataset": source.repo,
                "config": source.config,
                "split": source.split,
                "offset": offset,
                "length": PAGE,
            }
        )
        page = fetch(f"{ROWS_API}?{query}")
        batch = [item["row"] for item in page.get("rows", [])]
        rows.extend(batch)
        total = page.get("num_rows_total", len(rows))
        offset += len(batch)
        if not batch or offset >= total:
            return rows


def load_hf(
    name: str, *, fetch: Callable[[str], Any] = _get_json, use_cache: bool = True
) -> list[Sample]:
    source = HF_SOURCES[name]
    cache_file = CACHE / f"{name}.json"
    if use_cache and cache_file.exists():
        rows = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        rows = _fetch_rows(source, fetch)
        if use_cache:
            CACHE.mkdir(exist_ok=True)
            cache_file.write_text(json.dumps(rows), encoding="utf-8")
    if rows and source.text_field not in rows[0]:
        raise RuntimeError(
            f"{source.repo} has no column {source.text_field!r} (columns: {sorted(rows[0])}); "
            "the dataset changed, update HF_SOURCES"
        )
    return [
        Sample(name, f"{name}-{i}", str(row[source.text_field]), bool(source.is_attack(row)))
        for i, row in enumerate(rows)
        if str(row.get(source.text_field) or "").strip()
    ]


def load_golden_samples() -> list[Sample]:
    return [
        Sample(
            "golden",
            s.id,
            s.input,
            bool(s.label.get("is_prompt_injection") or s.label.get("intent")),
        )
        for s in load_golden().samples
    ]


def load(names: list[str], *, limit: int | None = None) -> list[Sample]:
    samples: list[Sample] = []
    for name in names:
        rows = load_golden_samples() if name == "golden" else load_hf(name)
        samples.extend(rows[:limit] if limit else rows)
    return samples
