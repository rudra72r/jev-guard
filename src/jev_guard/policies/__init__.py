"""Builtin policies. Importing this package registers them for ``Policy.from_builtin``."""

from jev_guard.policies.base import Policy
from jev_guard.policies.general import GeneralPolicy

__all__ = ["GeneralPolicy", "Policy"]
