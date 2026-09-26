"""Builtin policies. Importing this package registers them for ``Policy.from_builtin``."""

from jev_guard.policies.agent_tools import AgentToolsPolicy
from jev_guard.policies.base import Policy
from jev_guard.policies.coding_agent import CodingAgentPolicy
from jev_guard.policies.general import GeneralPolicy
from jev_guard.policies.general_local import GeneralLocalPolicy
from jev_guard.policies.rag import RagPolicy
from jev_guard.policies.support_agent import SupportAgentPolicy
from jev_guard.policies.writing_app import WritingAppPolicy

BUILTIN_POLICIES = (
    "general",
    "general_local",
    "writing_app",
    "support_agent",
    "coding_agent",
    "rag",
    "agent_tools",
)

__all__ = [
    "BUILTIN_POLICIES",
    "AgentToolsPolicy",
    "CodingAgentPolicy",
    "GeneralLocalPolicy",
    "GeneralPolicy",
    "Policy",
    "RagPolicy",
    "SupportAgentPolicy",
    "WritingAppPolicy",
]
