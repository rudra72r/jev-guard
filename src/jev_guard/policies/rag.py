"""The ``rag`` policy: retrieval-augmented answers that must stay grounded in the documents."""

from __future__ import annotations

from pydantic import Field

from jev_guard.policies.base import Policy
from jev_guard.policies.general import INPUT_PII, INTENT, PROMPT_INJECTION
from jev_guard.types import QuestionSpec

GROUNDED = QuestionSpec(
    type="noul",
    instructions=(
        "Every factual claim in the assistant_response is supported by the documents in context."
    ),
    severity="high",
    threshold=0.5,
    comparator="<",
    risk_when="low",
)

HAS_CITATION = QuestionSpec(
    type="noul",
    instructions=(
        "The assistant_response points to which document or passage in context its answer "
        "comes from (a citation, quote, or document reference)."
    ),
    severity="low",
    threshold=0.5,
    comparator="<",
    risk_when="low",
)

HALLUCINATION_RISK = QuestionSpec(
    type="score",
    instructions=(
        "How much of the assistant_response is invented rather than taken from the documents "
        "in context."
    ),
    criteria=[
        "Nothing invented: everything comes from context.",
        "Minor additions that don't change the meaning.",
        "Some specific details (names, numbers, dates, policies) are not in context.",
        "The main answer is not in context at all.",
    ],
    severity="high",
    threshold=2,
    comparator=">=",
)

CONTEXT_SUFFICIENT = QuestionSpec(
    type="noul",
    instructions="The documents in context contain enough information to answer user_message.",
    severity="medium",
    threshold=0.5,
    comparator="<",
    risk_when="low",
)

CONTRADICTS_CONTEXT = QuestionSpec(
    type="noul",
    instructions=(
        "The assistant_response states something that the documents in context contradict."
    ),
    severity="high",
    threshold=0.6,
)


class RagPolicy(Policy):
    """Guardrails for retrieval-augmented generation (internal knowledge bases, doc Q&A).

    Use case: a bank's internal assistant answering from policy documents, where an answer
    that isn't backed by the documents is worse than no answer. Pass the retrieved documents
    as ``context`` to ``check_output``; that is the whole point of this policy.

    Tradeoffs: ungrounded answers, likely hallucinations (>= 2 of 3), and contradictions are
    ``high``: one means review, two together block (sum 4.0 > ``block_threshold`` 3.0).
    Missing citations and thin context only lower confidence, because many good answers don't
    cite, and "the docs don't cover this" is a retrieval problem, not a safety one.
    Without ``context``, output checks fall back to ``general`` and log a warning.

    Tuning: for regulated answers, set ``block_threshold`` to 1.5 so any single grounding
    failure blocks, and promote ``contains_citation`` to ``high`` if every answer must cite.
    """

    name: str = "rag"
    version: str = "1.0.0"
    description: str = "RAG answers: checks grounding, hallucination, and contradiction."
    input: dict[str, QuestionSpec] = Field(
        default_factory=lambda: {
            "is_prompt_injection": PROMPT_INJECTION,
            "contains_pii": INPUT_PII,
            "intent": INTENT,
        }
    )
    output: dict[str, QuestionSpec] = Field(
        default_factory=lambda: {
            "answer_grounded_in_context": GROUNDED,
            "contains_citation": HAS_CITATION,
            "hallucination_risk": HALLUCINATION_RISK,
            "context_is_sufficient": CONTEXT_SUFFICIENT,
            "answer_contradicts_context": CONTRADICTS_CONTEXT,
        }
    )
    review_threshold: float = 1.0
    block_threshold: float | None = 3.0
    suggested_responses: dict[str, str] = Field(
        default_factory=lambda: {
            "is_prompt_injection": "I can only answer questions about the documents I have.",
            "intent": "Sorry, I can't help with that request.",
        }
    )
    default_suggested_response: str | None = (
        "I couldn't find a reliable answer to that in the documents I have."
    )
    context_fallback: str | None = "general"
