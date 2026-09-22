"""The ``support_agent`` policy: customer-support bots that must not create liability."""

from __future__ import annotations

from pydantic import Field

from jev_guard.policies.base import Policy
from jev_guard.policies.general import (
    INPUT_PII,
    INTENT,
    MATCHES_USER_INTENT,
    OFF_TOPIC,
    OUTPUT_PII,
    PROMPT_INJECTION,
)
from jev_guard.types import QuestionSpec

FRUSTRATION_LEVEL = QuestionSpec(
    type="score",
    instructions="How frustrated the customer is in user_message.",
    criteria=[
        "Calm, just asking or stating facts.",
        "Mildly annoyed but civil.",
        "Clearly frustrated or upset.",
        "Very angry: strong language, threats to leave, or repeated complaints.",
    ],
    severity="high",
    threshold=2,
    comparator=">=",
)

SHOULD_ESCALATE = QuestionSpec(
    type="noul",
    instructions=(
        "The user_message asks for a human, a manager, or a formal complaint, or describes a "
        "problem a support bot should not handle alone (account security, legal threats, "
        "safety issues, or charges the customer disputes)."
    ),
    severity="high",
    threshold=0.5,
)

REFUND_PROMISE = QuestionSpec(
    type="noul",
    instructions=(
        "The assistant_response promises, approves, or guarantees a refund, credit, discount, "
        "or compensation."
    ),
    severity="high",
    threshold=0.7,
)

LEGAL_OR_MEDICAL = QuestionSpec(
    type="noul",
    instructions=(
        "The assistant_response gives legal advice or medical advice: telling the customer what "
        "the law requires of them, what their legal rights or options are, or what treatment, "
        "dosage, or diagnosis applies to them."
    ),
    severity="critical",
    threshold=0.6,
)

SLA_COMMITMENT = QuestionSpec(
    type="noul",
    instructions=(
        "The assistant_response commits the company to a specific deadline, uptime, response "
        "time, or delivery date (for example 'fixed within 24 hours' or '99.99% uptime')."
    ),
    severity="critical",
    threshold=0.7,
)


class SupportAgentPolicy(Policy):
    """Guardrails for customer-support agents.

    Use case: a support bot on OpenAI or Anthropic that needs to be "enterprise-safe" before a
    big customer signs. Adds liability checks on top of ``general``.

    Tradeoffs: legal/medical advice (> 0.6) and SLA commitments (> 0.7) block outright, because
    one bad reply creates real legal exposure. Refund promises, frustrated customers
    (frustration >= 2), and escalation requests (> 0.5) go to ``review``, so a human can step
    in without the bot refusing to talk. Two review signals together don't block
    (no ``block_threshold``), because an angry customer asking for a manager is exactly who
    you want a human to see, not a refusal.

    Tuning: for teams that can grant refunds, set ``contains_refund_promise`` to 0.9 or drop it
    from a YAML copy; for B2B support with contractual SLAs, lower ``contains_sla_commitment``.
    """

    name: str = "support_agent"
    version: str = "1.0.0"
    description: str = "Customer support: blocks legal/medical advice and SLA promises."
    input: dict[str, QuestionSpec] = Field(
        default_factory=lambda: {
            "is_prompt_injection": PROMPT_INJECTION,
            "contains_pii": INPUT_PII,
            "intent": INTENT,
            "frustration_level": FRUSTRATION_LEVEL,
            "should_escalate_to_human": SHOULD_ESCALATE,
        }
    )
    output: dict[str, QuestionSpec] = Field(
        default_factory=lambda: {
            "contains_pii": OUTPUT_PII,
            "is_off_topic": OFF_TOPIC,
            "matches_user_intent": MATCHES_USER_INTENT,
            "contains_refund_promise": REFUND_PROMISE,
            "contains_legal_or_medical_advice": LEGAL_OR_MEDICAL,
            "contains_sla_commitment": SLA_COMMITMENT,
        }
    )
    review_threshold: float = 1.0
    block_threshold: float | None = None
    suggested_responses: dict[str, str] = Field(
        default_factory=lambda: {
            "is_prompt_injection": "I can only help with questions about your account or order.",
            "intent": "Sorry, I can't help with that request.",
            "contains_legal_or_medical_advice": (
                "I'm not able to give legal or medical advice. Let me connect you with someone "
                "who can help."
            ),
            "contains_sla_commitment": (
                "I can't promise a specific timeline, but I've passed this on to the team and "
                "someone will follow up with you."
            ),
        }
    )
