# Benchmark: jev-guard vs the alternatives

One question, four ways to answer it: **is this message a prompt-injection or jailbreak
attack?**

| detector | what it is | cost |
|---|---|---|
| `regex` | keyword and regex rules, the usual starting point | free |
| `jev` | jev-guard (`general` policy by default); an attack counts as detected when the verdict **blocks** | Jev API |
| `judge` | an LLM asked to classify each message (Claude Haiku 4.5 by default) | Anthropic API |
| `llm-guard` | Protect AI's LLM Guard `PromptInjection` scanner, a local model | your CPU/GPU |

## Datasets

| name | source | licence | rows | notes |
|---|---|---|---|---|
| `golden` | jev-guard's own eval set | MIT | 100 | 50 attacks; PII samples count as benign |
| `deepset` | [deepset/prompt-injections](https://huggingface.co/datasets/deepset/prompt-injections) test | Apache-2.0 | 116 | some non-English rows, a few debatable labels |
| `jackhhao` | [jackhhao/jailbreak-classification](https://huggingface.co/datasets/jackhhao/jailbreak-classification) test | Apache-2.0 | 262 | role-play jailbreaks vs benign persona prompts |
| `gandalf` | [Lakera/gandalf_ignore_instructions](https://huggingface.co/datasets/Lakera/gandalf_ignore_instructions) test | MIT | 112 | attacks only: detection rate, no false-alarm rate |

## Run it

```bash
python -m benchmarks.run                        # free: plan, cost estimate, regex baseline
export TYPESAFE_API_KEY=sk-... ANTHROPIC_API_KEY=sk-ant-...
pip install llm-guard                           # optional, downloads a model
python -m benchmarks.run --run --max-cost 0.50  # everything, capped
```

Without `--run`, nothing paid is called. With it, the estimated total must be under
`--max-cost`, and each detector stops at its share of the budget. Results go to
`benchmarks/results/` as Markdown and JSON.

Estimated cost of a full run over all 590 samples (computed by the harness's own
estimators): **$0.0101 for jev-guard** and **$0.1827 for the Haiku judge** at $1 / $5 per
million tokens, so the judge costs about 18× more on identical data. Check current prices and pass
`--judge-price-in` / `--judge-price-out` if they've changed.

## Regex baseline (measured)

The free baseline, run on 2026-09-22 over all four datasets:

| dataset | detection rate | false alarms |
|---|---:|---:|
| golden | 46% | 2% |
| deepset | 5% | 0% |
| jackhhao | 60% | 0% |
| gandalf | 50% | – |
| **all** | **46%** | **0%** |

Regex almost never cries wolf, but it misses more than half of all attacks. That's the bar.

## Reading the results fairly

- **Report every dataset, not just the total.** Public labels are noisy, and a detector can
  look great on one set and poor on another.
- **Detection rate and false-alarm rate matter together.** A detector that flags everything
  detects 100%.
- **The judge's prompt is in `detectors.py`.** A better prompt or a bigger model will do
  better, at a higher cost and latency; that's the tradeoff being measured.
- **jev-guard here uses a stock policy with no tuning on these datasets.**
