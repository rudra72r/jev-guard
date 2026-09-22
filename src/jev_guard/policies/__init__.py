"""Builtin policies. Importing this package registers them for ``Policy.from_builtin``."""

from jev_guard.policies.base import Policy
from jev_guard.policies.coding_agent import CodingAgentPolicy
from jev_guard.policies.general import GeneralPolicy
from jev_guard.policies.rag import RagPolicy
from jev_guard.policies.support_agent import SupportAgentPolicy
from jev_guard.policies.writing_app import WritingAppPolicy

BUILTIN_POLICIES = ("general", "writing_app", "support_agent", "coding_agent", "rag")

__all__ = [
    "BUILTIN_POLICIES",
    "CodingAgentPolicy",
    "GeneralPolicy",
    "Policy",
    "RagPolicy",
    "SupportAgentPolicy",
    "WritingAppPolicy",
]
