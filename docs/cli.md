# Command line

```bash
pip install "jev-guard[cli]"
jev-guard --help
```

Every command exits 0 on success and 1 on a user error, printing a one-line "next step". Tracebacks only appear with `--debug`.

## `check`: one-off checks

```bash
jev-guard check "message" [--policy NAME|FILE.yaml] [--output "reply"] [--context "doc" ...]
                          [--json] [--fail-on never|review|block]
```

`--fail-on block` exits with code 3 when anything is blocked, for shell scripts and CI hooks. A block on its own isn't an error, so without it the exit code is 0.

## `scan`: batch-check logs

```bash
jev-guard scan logs.jsonl [--policy NAME] [--out report.html] [--format html|json|md]
                          [--dry-run] [--max-cost 1.00] [--concurrency 4]
```

- **Input**: JSONL, one record per line. The format is detected from the first record:
    - native: `{"input": ..., "output": ..., "context": [...], "label": ...}`
    - Langfuse trace or observation exports
    - Arize / OpenInference span exports (flat `attributes.input.value` or nested)
- **Report**: counts by action, the 20 worst offenders with their reasons, total cost, a latency histogram, and, when records have a `label`, precision / recall / F1 per question.
- **Labels**: `"label": "block"` (any of `allow` / `review` / `block`, or `true` / `false`) scores flagged vs not flagged. `"label": {"is_prompt_injection": true}` scores individual questions.
- **Cost safety**: the cost is estimated locally first. `--dry-run` stops there and makes no Jev calls. A scan whose estimate is over `--max-cost` refuses to start, and a running scan stops when actual spend reaches it. Records that fail (rate limits, timeouts) are reported and don't count against the cap. A bad API key stops the scan at the first record.

## `eval`: the golden dataset

```bash
jev-guard eval [--policy NAME] [--dataset DIR|FILE.jsonl] [--out eval.html]
               [--dry-run] [--max-cost 0.10]
```

The builtin set has 100 hand-written samples:

- 50 attacks across 9 patterns (instruction override, prompt extraction, role-play, fake authority, indirect injection, encodings, payload splitting, refusal suppression, malicious requests)
- 30 clean messages, half of them deliberate look-alikes such as "Ignore the typo in my last message"
- 20 with synthetic personal data

The command prints precision / recall / F1 per question and the total cost, then lists every mistake by sample ID. It **exits 4** if any score falls below the minimum in the dataset's `manifest.json`, so CI can tell a regression from a typo. A full run costs about $0.001.

Custom datasets use the same line format as the builtin one: `{"id", "input", "output"?, "context"?, "label": {question: bool}, "category"?}`.

## `policy`

```bash
jev-guard policy list                  # the builtins
jev-guard policy show support_agent    # print as YAML (works on a .yaml path too)
jev-guard policy validate my.yaml      # every problem, with line numbers and suggestions
```
