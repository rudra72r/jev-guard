"""Offline backend: local transformer models answer the policy's questions. Free and private.

    pip install "jev-guard[local]"     # transformers + torch; models download on first use

Two kinds of model:

- **Specialists** answer the questions they were trained for. By default,
  ``protectai/deberta-v3-base-prompt-injection-v2`` (Apache-2.0) answers
  ``is_prompt_injection``, ``is_multi_turn_injection``, and ``contains_injected_instructions``,
  reading the relevant field of the state.
- **A zero-shot NLI model** answers every other question: a yes/no question is the
  probability that its statement is entailed by the content, and a choice or score question
  is a classification over its options or levels. Default:
  ``MoritzLaurer/deberta-v3-base-zeroshot-v2.0`` (MIT).

Tradeoffs versus Jev: no API, no cost, nothing leaves the machine, but answers are less
nuanced and it is **much slower on CPU**. Measured on a 4-thread laptop CPU with the
defaults: ~0.3 s for a specialist question, ~1.7 s per zero-shot yes/no question, and ~2.9 s
for a 3-option choice, so a full `general` input check is about 5 s. A GPU, or the smaller
``local:MoritzLaurer/deberta-v3-xsmall-zeroshot-v1.1-all-33`` (roughly 3x faster, less
accurate), cuts that down. Use it for offline work, batch scanning, and as a fallback when
Jev is unreachable, not usually on a latency-critical path.

NLI models read ~512 tokens, so long content is truncated for zero-shot questions;
specialists scan long text in windows and keep the highest score. Measure quality on your
own data with ``jev-guard eval --backend local``.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from jev_guard.client import JevResult, WireQuestions
from jev_guard.errors import ConfigurationError
from jev_guard.types import JevAnswer

DEFAULT_NLI_MODEL = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"
INJECTION_MODEL = "protectai/deberta-v3-base-prompt-injection-v2"
WINDOW_CHARS = 1_500  # ~400 tokens: fits a 512-token classifier with room to spare

Pipeline = Callable[..., Any]
PipelineFactory = Callable[[str, str], Pipeline]


@dataclass(frozen=True, slots=True)
class Specialist:
    """A text classifier that answers one question from one state field (``*`` = all)."""

    model: str
    positive_label: str
    field: str = "*"


@dataclass(frozen=True, slots=True)
class RegexSpecialist:
    """Answers a PII question with ``jev_guard.redact``'s patterns: no model, no latency.

    Measured on the golden set, regex alone answers ``contains_pii`` with precision 1.00 and
    recall 0.80, against 0.20 recall from the zero-shot model: it catches emails, phone
    numbers, SSNs, and payment cards exactly, but can't see a name or street address.
    With ``nli_fallback`` (the default), a text with no pattern match is then put to the
    zero-shot model, which catches some of those. The slow path only runs when regex finds
    nothing.
    """

    field: str = "*"
    hit: float = 0.95
    miss: float = 0.02
    nli_fallback: bool = True


Answerer = Specialist | RegexSpecialist

DEFAULT_SPECIALISTS: dict[str, Answerer] = {
    "is_prompt_injection": Specialist(INJECTION_MODEL, "INJECTION", "user_message"),
    "is_multi_turn_injection": Specialist(INJECTION_MODEL, "INJECTION", "*"),
    "contains_injected_instructions": Specialist(INJECTION_MODEL, "INJECTION", "tool_result"),
    "contains_pii": RegexSpecialist(),
    "contains_secrets": RegexSpecialist(),
}


def _transformers_pipeline(task: str, model: str) -> Pipeline:
    try:
        from transformers import pipeline  # noqa: PLC0415 (optional, heavy)
    except ImportError as err:
        raise ConfigurationError(
            "The local backend needs transformers and torch.",
            hint='pip install "jev-guard[local]"',
        ) from err
    # `task` is dynamic, so it can't match transformers' per-task overloads; and the import
    # is absent in CI, where a `type: ignore` would itself be flagged as unused.
    factory = cast("Any", pipeline)
    return cast("Pipeline", factory(task, model=model))


def _field_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, Mapping) and "content" in item:
                parts.append(f"{item.get('role', 'user')}: {item['content']}")
            else:
                parts.append(_field_text(item))
        return "\n".join(parts)
    return json.dumps(value, ensure_ascii=False, default=str)


def render_state(state: Mapping[str, Any]) -> str:
    """The state as plain text, one ``field: value`` block per field, for NLI premises."""
    return "\n".join(f"{key}: {_field_text(value)}" for key, value in state.items())


def _windows(text: str) -> list[str]:
    if len(text) <= WINDOW_CHARS:
        return [text]
    step = WINDOW_CHARS - 200
    return [text[i : i + WINDOW_CHARS] for i in range(0, len(text) - 200, step)]


class LocalBackend:
    """Answers typed questions with local transformer models (lazy-loaded, thread-safe)."""

    needs_typesafe_key = False
    input_price_per_million = 0.0

    def __init__(
        self,
        nli_model: str = DEFAULT_NLI_MODEL,
        specialists: Mapping[str, Answerer] | None = None,
        *,
        pipeline_factory: PipelineFactory | None = None,
    ) -> None:
        self.nli_model = nli_model
        self.specialists = dict(DEFAULT_SPECIALISTS if specialists is None else specialists)
        self._factory = pipeline_factory or _transformers_pipeline
        self._pipelines: dict[tuple[str, str], Pipeline] = {}
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return f"local:{self.nli_model.rsplit('/', 1)[-1]}"

    def _pipeline(self, task: str, model: str) -> Pipeline:
        key = (task, model)
        with self._lock:
            if key not in self._pipelines:
                self._pipelines[key] = self._factory(task, model)
            return self._pipelines[key]

    # --- answering ------------------------------------------------------------------------

    @staticmethod
    def _text_for(field: str, state: Mapping[str, Any]) -> str:
        if field == "*" or field not in state:
            return render_state(state)
        return _field_text(state[field])

    def _regex_answer(
        self, spec: RegexSpecialist, text: str, question: Mapping[str, object]
    ) -> JevAnswer:
        from jev_guard.redact import _regex_hits  # noqa: PLC0415 (import cycle)

        if _regex_hits(text):
            return JevAnswer(type="noul", noul=spec.hit)
        if spec.nli_fallback:  # no pattern matched: ask the model about names, addresses...
            answer = self._nli(question, text)
            if answer is not None and answer.noul is not None:
                return JevAnswer(type="noul", noul=max(answer.noul, spec.miss))
        return JevAnswer(type="noul", noul=spec.miss)

    def _specialist(
        self, spec: Answerer, state: Mapping[str, Any], question: Mapping[str, object]
    ) -> JevAnswer:
        text = self._text_for(spec.field, state)
        if isinstance(spec, RegexSpecialist):
            return self._regex_answer(spec, text, question)
        classify = self._pipeline("text-classification", spec.model)
        best = 0.0
        for window in _windows(text):
            result = classify(window, truncation=True, top_k=None)  # every label's score
            scores = {str(r["label"]).upper(): float(r["score"]) for r in _as_list(result)}
            best = max(best, scores.get(spec.positive_label.upper(), 0.0))
        return JevAnswer(type="noul", noul=best)

    def _zero_shot(self, premise: str, labels: list[str], *, multi_label: bool) -> dict[str, float]:
        classify = self._pipeline("zero-shot-classification", self.nli_model)
        out = classify(
            premise[: WINDOW_CHARS * 2],
            candidate_labels=labels,
            hypothesis_template="{}",
            multi_label=multi_label,
            # One forward pass per label; batching them together is ~2x faster on CPU.
            batch_size=len(labels),
        )
        return {
            str(label): float(score)
            for label, score in zip(out["labels"], out["scores"], strict=True)
        }

    def _nli(self, question: Mapping[str, object], premise: str) -> JevAnswer | None:
        kind = question.get("type")
        instructions = str(question.get("instructions") or "")
        criteria = question.get("criteria")
        if kind == "noul":
            scores = self._zero_shot(premise, [instructions], multi_label=True)
            return JevAnswer(type="noul", noul=scores.get(instructions, 0.0))
        if kind == "choice" and isinstance(criteria, Mapping) and criteria:
            texts = {
                f"{label}: {desc}" if desc else str(label): str(label)
                for label, desc in criteria.items()
            }
            scores = self._zero_shot(premise, list(texts), multi_label=False)
            dist = {texts[t]: p for t, p in scores.items()}
            best = max(dist, key=dist.__getitem__)
            return JevAnswer(type="choice", choice=best, confidence=dist[best], probabilities=dist)
        if kind == "score" and isinstance(criteria, list) and criteria:
            texts = {str(desc): str(i) for i, desc in enumerate(criteria)}
            scores = self._zero_shot(premise, list(texts), multi_label=False)
            dist = {texts[t]: p for t, p in scores.items()}
            return JevAnswer(
                type="score",
                score=sum(int(level) * p for level, p in dist.items()),
                confidence=max(dist.values()),
                probabilities=dist,
            )
        return None

    def evaluate(self, state: Mapping[str, Any], questions: WireQuestions) -> JevResult:
        start = time.perf_counter()
        premise = render_state(state)
        answers: dict[str, JevAnswer] = {}
        for name, question in questions.items():
            spec = self.specialists.get(name)
            answer = (
                self._specialist(spec, state, question)
                if spec is not None
                else self._nli(question, premise)
            )
            if answer is not None:
                answers[name] = answer
        return JevResult(
            answers=answers,
            input_tokens=0,
            model=self.name,
            latency_ms=(time.perf_counter() - start) * 1000,
            cost_usd=0.0,
        )

    async def aevaluate(self, state: Mapping[str, Any], questions: WireQuestions) -> JevResult:
        return await asyncio.to_thread(self.evaluate, state, questions)

    def close(self) -> None:
        """Models stay loaded for reuse; nothing to release per call."""


def _as_list(result: Any) -> list[Mapping[str, Any]]:
    """Pipelines return a dict, a list of dicts, or a list of lists, depending on version."""
    if isinstance(result, Mapping):
        return [result]
    if isinstance(result, list) and result and isinstance(result[0], list):
        return list(result[0])
    return list(result)
