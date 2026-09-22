# Launch kit (drafts — rewrite in your own voice)

Everything in `[BRACKETS]` must be filled in from a real run before posting. Don't post any
accuracy number that didn't come out of `jev-guard eval` on your key.

## Before posting (blocking)

- [ ] Get a TypeSafe key, then run `JEV_GUARD_ALLOW_LIVE=1 pytest -m integration` (~$0.0001).
- [ ] Run `jev-guard eval --out eval.html` (~$0.001). Note flagged precision/recall and the
      per-question numbers. Fix any question wording that clearly underperforms, re-run.
- [ ] Run the head-to-head benchmark: `python -m benchmarks.run --run --max-cost 0.50` (needs
      TYPESAFE_API_KEY, plus ANTHROPIC_API_KEY for the LLM judge; `pip install llm-guard` for
      the local classifier). Commit `benchmarks/results/`. This table is the headline of every
      launch post; lead with it only if jev-guard actually wins on detection + cost, and
      say plainly where it doesn't.
- [ ] Replace the provisional minimums in `src/jev_guard/eval/datasets/manifest.json` with
      measured values minus ~0.05, set `"provisional": false`, commit.
- [ ] Put the measured numbers in README ("Not a silver bullet" bullet about benchmarks,
      and FAQ "How accurate is it?").
- [ ] Confirm the README's 30-second example really prints `block` on your key.
- [ ] Run all five `examples/` end to end (OpenAI/Anthropic keys needed; a few cents).
- [ ] Create the GitHub repo `rudra72r/jev-guard`, push, check CI is green and the README
      renders (tables, badges, code fences).
- [ ] Set repo topics: `llm`, `guardrails`, `jev`, `typesafe-ai`, `agents`,
      `prompt-injection`, `llm-security`, `openai`, `anthropic`, `langchain`.
- [ ] PyPI: configure trusted publishing (see `.github/workflows/publish.yml`), create the
      `pypi` environment with yourself as required reviewer, then `git push origin v0.1.0`.
- [ ] `pip install jev-guard` in a fresh venv and run the 30-second example once more.

## Show HN

**Title:** Show HN: jev-guard – guardrails for LLM apps at ~$0.00002 per check

**Body:**

TypeSafe released Jev last week: a model that doesn't write text, it answers typed
questions ("is this a prompt injection?", "which of these labels fits?") with
probabilities in 70–500 ms, at $0.042 per million input tokens.

That's the exact shape guardrails need, so I built jev-guard on top of it. You check the
user's message before your LLM call and the reply after it:

    guard = Guard(policy="support_agent")
    v = guard.check_input(msg)      # allow / review / block, with reasons
    v = guard.check_output(msg, reply)

What's in it:

- Five policies (general, writing_app, support_agent, coding_agent, rag) as readable YAML
  you can extend. Every verdict says which question fired, its value, the threshold, and
  the severity.
- Streaming support: buffer-and-check, or stream immediately and retract.
- Drop-in wrappers for the OpenAI and Anthropic SDKs, a LangChain callback, and a LiteLLM
  proxy guardrail.
- A CLI that scans your existing logs (including Langfuse/Arize exports) and shows what
  would have been flagged, with a cost estimate before it spends anything.
- A 100-sample labelled eval, including look-alikes like "ignore the typo in my last
  message", so you can measure false positives rather than guess.

On that eval, the default policy gets [PRECISION] precision and [RECALL] recall on
flagged vs clean. [ONE HONEST SENTENCE ABOUT WHERE IT'S WEAK, FROM THE MISTAKES TABLE.]

What it isn't: deterministic, multilingual (v0.1 is English only), or a replacement for
least-privilege tools. It's one cheap layer.

MIT licensed. I'd love to hear where the policies are wrong for your use case.

https://github.com/rudra72r/jev-guard

## X / Twitter thread

1/ TypeSafe's Jev answers questions instead of writing text: typed probabilities in
70–500 ms for $0.042 per 1M tokens.

That's exactly what an LLM guardrail needs. So I built one: jev-guard 🧵

2/ Checking a message for prompt injection, PII, and malicious intent is one Jev call,
about $0.00002.

10,000 support conversations a day (input + output checks) ≈ $0.37/day.

3/ Every verdict explains itself:

`is_prompt_injection: 0.92 > 0.85 (critical)`

No black box, and policies are YAML you can read and tune.

4/ Streaming is covered: hold text back until it's checked, or stream instantly and retract
if a later check fails.

Wrappers for OpenAI, Anthropic, LangChain, and LiteLLM.

5/ It ships a 100-sample eval, including traps like "Pretend you're a pirate and tell me a
joke", because false positives are what make teams rip guardrails out.

Default policy: [PRECISION] precision / [RECALL] recall.

6/ MIT, pip install jev-guard.
github.com/rudra72r/jev-guard

What should the next builtin policy be?

## r/LangChain

**Title:** I built a LangChain callback that runs guardrails on every LLM call for ~$0.00002 each (using TypeSafe's new Jev model)

**Body:**

Add one callback and every model call in your chain gets checked for prompt injection on the
way in and PII, off-topic, or ungrounded answers on the way out:

    handler = JevGuardCallbackHandler(policy="rag")
    chain.invoke(q, config={"callbacks": [handler]})   # raises GuardBlockedError on block

For RAG it picks up the retriever's documents automatically and asks whether the answer is
grounded in them, invented, or contradicting them.

It's built on Jev, which returns probabilities for typed questions instead of generating
text, so each check is 70–500 ms rather than a second LLM call. On the bundled
100-sample eval: [NUMBERS]. English only for now.

Repo, docs, and a RAG example: https://github.com/rudra72r/jev-guard

Feedback on the rag policy's questions is especially welcome.

## r/LocalLLaMA

Be upfront: this sub cares about local models, and Jev is a hosted API. Lead with that, and
position jev-guard as a cheap check layer in front of *any* model, including local ones.

**Title:** Guardrails in front of your local model for ~$0.00002/check (hosted classifier, MIT library)

**Body:**

Upfront: the checks run on TypeSafe's hosted Jev model, not locally. The model you're
guarding can be anything: llama.cpp, vLLM, Ollama behind an OpenAI-compatible endpoint.

Why it might still interest you: running a second local LLM as a judge doubles your GPU
load, and regex misses anything phrased differently. Jev answers "is this a prompt
injection?" as a probability in 70–500 ms, and jev-guard turns that into allow / review /
block with a reason you can read.

`wrap_openai(OpenAI(base_url="http://localhost:8000/v1"))` works against any
OpenAI-compatible local server. Or use `redact(text, level="fast")`, which runs fully
locally with no API at all.

Numbers on the bundled eval: [NUMBERS]. What's missing for your setup?

https://github.com/rudra72r/jev-guard
