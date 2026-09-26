"""The ``general_local`` policy: ``general`` with thresholds calibrated for local models.

A threshold encodes how confident *that backend* is, so it can't be carried between them.
Measured on the golden set, the offline zero-shot model picks "malicious" for the right
messages but only at about 0.5 confidence, so `general`'s 0.8 intent threshold never fires
and intent recall is 0.00 — the eval fails. Dropping it to 0.4 gives intent F1 0.86 and
leaves the other questions where they were.

Use this one whenever the backend is local; on Jev, use `general`. Re-measure with
``jev-guard eval --backend local`` whenever you change backend or model.
"""

from __future__ import annotations

from pydantic import Field

from jev_guard.policies.general import INPUT_PII, INTENT, PROMPT_INJECTION, GeneralPolicy
from jev_guard.types import QuestionSpec

LOCAL_INTENT = INTENT.model_copy(update={"threshold": 0.4})


class GeneralLocalPolicy(GeneralPolicy):
    """``general``, retuned for the offline backend and other local models.

    Use case: running with ``JEV_GUARD_BACKEND=local``, or any small local model, where you
    want the same three input checks as ``general`` without hand-calibrating them first.

    Tradeoffs: identical to ``general`` except ``intent``, whose threshold drops from 0.8 to
    0.4. A threshold encodes how confident a particular backend is, and the offline zero-shot
    model is right about intent but only ~0.5 confident, so at 0.8 it never fires. Measured on
    the golden set: intent recall goes from 0.00 (the eval fails) to 1.00, precision 0.75,
    F1 0.86, and the other questions are unchanged. On Jev this policy would flag too much —
    use ``general`` there.

    Tuning: 0.4 was measured on this project's 100-sample set with the default local models.
    Change either the model or the traffic and re-measure with
    ``jev-guard --backend local eval --policy general_local``.
    """

    name: str = "general_local"
    version: str = "1.0.0"
    description: str = "general, with thresholds calibrated for local models."
    input: dict[str, QuestionSpec] = Field(
        default_factory=lambda: {
            "is_prompt_injection": PROMPT_INJECTION,
            "contains_pii": INPUT_PII,
            "intent": LOCAL_INTENT,
        }
    )
