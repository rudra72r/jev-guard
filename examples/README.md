# Examples

Each file runs on its own. Start with the first one — it needs no account and no key.

| file | what it shows | needs |
|---|---|---|
| [00_try_it_offline.py](00_try_it_offline.py) | Four messages checked end to end, entirely on your machine | the `[local]` extra |
| [01_openai_chat_wrapped.py](01_openai_chat_wrapped.py) | A support bot with input and output checks around an OpenAI call | `OPENAI_API_KEY` |
| [02_anthropic_agent.py](02_anthropic_agent.py) | The `@guarded` decorator, then an agent whose shell commands are checked before they run | `ANTHROPIC_API_KEY` |
| [03_langchain_rag.py](03_langchain_rag.py) | A LangChain callback that checks answers for grounding in the retrieved documents | LangChain + `ANTHROPIC_API_KEY` |
| [04_streaming_openai.py](04_streaming_openai.py) | Both streaming strategies: buffer-and-check, and stream-then-retract | `OPENAI_API_KEY` |
| [05_batch_scan.py](05_batch_scan.py) | Scoring a whole log file to pick thresholds before going live; estimates cost for free first | nothing, to estimate |

Every example except the first also needs a backend for the checks themselves: either
`TYPESAFE_API_KEY` for Jev, or `JEV_GUARD_BACKEND=local` to keep them offline.

Installing an extra, while jev-guard is git-only:

```bash
pip install "jev-guard[local] @ git+https://github.com/rudra72r/jev-guard"
```
