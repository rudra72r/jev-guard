"""The ``writing_app`` policy: zero-config defaults for creative and writing tools."""

from __future__ import annotations

from pydantic import Field

from jev_guard.policies.base import Policy
from jev_guard.policies.general import (
    INPUT_PII,
    INTENT,
    OFF_TOPIC,
    OUTPUT_PII,
    PROMPT_INJECTION,
)
from jev_guard.types import QuestionSpec


class WritingAppPolicy(Policy):
    """Guardrails for writing assistants, story tools, and other creative apps.

    Use case: an indie writing app on top of Claude or GPT, where the main worry is users
    prompt-injecting the system prompt. Writers type strange things on purpose, so this
    policy stays out of their way.

    Tradeoffs: same input checks as ``general``. On output, ``matches_user_intent`` is dropped
    (writing requests are loose, and "rewrite this but weirder" shouldn't look like a miss),
    and ``is_off_topic`` only fires above 0.85 (tangents are normal in fiction). PII questions
    ask about *real* people, so a character's made-up address shouldn't fire.

    Tuning: if your users paste real manuscripts with real contact details, raise
    ``contains_pii`` to 0.8 to cut reviews.
    """

    name: str = "writing_app"
    version: str = "1.0.0"
    description: str = "Minimal defaults for writing tools: stops prompt injection, stays quiet."
    input: dict[str, QuestionSpec] = Field(
        default_factory=lambda: {
            "is_prompt_injection": PROMPT_INJECTION,
            "contains_pii": INPUT_PII,
            "intent": INTENT,
        }
    )
    output: dict[str, QuestionSpec] = Field(
        default_factory=lambda: {
            "contains_pii": OUTPUT_PII,
            "is_off_topic": OFF_TOPIC.model_copy(update={"threshold": 0.85}),
        }
    )
    review_threshold: float = 1.0
    block_threshold: float | None = None
    suggested_responses: dict[str, str] = Field(
        default_factory=lambda: {
            "is_prompt_injection": (
                "I can't change how I've been set up, but I'm happy to keep writing with you."
            ),
            "intent": "Sorry, I can't help with that request.",
        }
    )
