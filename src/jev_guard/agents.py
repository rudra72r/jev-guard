"""Guard an agent's tool traffic: check tool calls before they run and tool results before
the model reads them.

    from jev_guard.agents import ToolGuard

    tools = ToolGuard()                       # uses the agent_tools policy

    v = tools.check_tool_call("run_shell", {"command": cmd}, user_request=task)
    if v.blocked: ...                         # don't run it
    if v.action == "review": ...              # ask a human first

    v = tools.check_tool_result("fetch_url", page_text, user_request=task)
    if v.blocked: ...                         # don't feed it to the model (indirect injection)

Tool calls are the model's *output* (actions it wants to take), so they're checked with the
policy's output questions and ``verdict.stage == "output"``. Tool results are *input* to
the model (untrusted content entering its context), so they use the input questions and
``stage == "input"``.

Long tool results are checked in overlapping windows, so an instruction hidden in the
middle of a large page is still seen. That costs one Jev call per ~40,000 characters. The
verdict returned is the most severe window's.
"""

from __future__ import annotations

import json
from types import TracebackType
from typing import Any

from jev_guard.guard import Guard
from jev_guard.integrations._common import ToolCall, text_of, tool_calls_in
from jev_guard.policies.agent_tools import NEEDS_USER_REQUEST
from jev_guard.policies.base import Policy
from jev_guard.types import Verdict

__all__ = ["ToolCall", "ToolGuard", "tool_calls_in"]

WINDOW_CHARS = 40_000  # ~10k tokens: well inside Jev's 32k-token state limit
WINDOW_OVERLAP = 1_000  # so an instruction split by a window edge is seen whole
_RANK = {"allow": 0, "review": 1, "block": 2}


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list | tuple):
        joined = text_of(value)
        if joined:
            return joined
    return json.dumps(value, ensure_ascii=False, default=str)


def _windows(text: str) -> list[str]:
    if len(text) <= WINDOW_CHARS:
        return [text]
    step = WINDOW_CHARS - WINDOW_OVERLAP
    return [
        text[start : start + WINDOW_CHARS] for start in range(0, len(text) - WINDOW_OVERLAP, step)
    ]


def _worst(verdicts: list[Verdict]) -> Verdict:
    return max(verdicts, key=lambda v: (_RANK[v.action], -v.confidence))


class ToolGuard:
    """Checks tool calls and tool results with a tool policy (``agent_tools`` by default)."""

    def __init__(self, policy: str | Policy = "agent_tools") -> None:
        self.guard = Guard(policy=policy)

    @property
    def policy(self) -> Policy:
        return self.guard.policy

    def _policy_for(self, user_request: str | None) -> Policy:
        if user_request:
            return self.policy
        output = {n: q for n, q in self.policy.output.items() if n not in NEEDS_USER_REQUEST}
        return self.policy.model_copy(update={"output": output})

    @staticmethod
    def _call_state(tool_name: str, arguments: Any, user_request: str | None) -> dict[str, Any]:
        state: dict[str, Any] = {"tool_name": tool_name, "tool_arguments": arguments}
        if user_request:
            state["user_request"] = user_request
        return state

    @staticmethod
    def _result_states(
        tool_name: str, result: Any, user_request: str | None
    ) -> list[dict[str, Any]]:
        states = []
        for window in _windows(_as_text(result)):
            state: dict[str, Any] = {"tool_name": tool_name, "tool_result": window}
            if user_request:
                state["user_request"] = user_request
            states.append(state)
        return states

    # --- sync -------------------------------------------------------------------------------

    def check_tool_call(
        self, tool_name: str, arguments: Any, user_request: str | None = None
    ) -> Verdict:
        """Check a tool call before running it. ``arguments``: a dict, JSON string, or text."""
        state = self._call_state(tool_name, arguments, user_request)
        blank = not tool_name.strip() and not _as_text(arguments).strip()
        return self.guard._run("output", state, blank, self._policy_for(user_request))

    def check_tool_result(
        self, tool_name: str, result: Any, user_request: str | None = None
    ) -> Verdict:
        """Check a tool's result before the model reads it."""
        states = self._result_states(tool_name, result, user_request)
        blank = not _as_text(result).strip()
        return _worst([self.guard._run("input", s, blank, self.policy) for s in states])

    # --- async ------------------------------------------------------------------------------

    async def acheck_tool_call(
        self, tool_name: str, arguments: Any, user_request: str | None = None
    ) -> Verdict:
        state = self._call_state(tool_name, arguments, user_request)
        blank = not tool_name.strip() and not _as_text(arguments).strip()
        return await self.guard._arun("output", state, blank, self._policy_for(user_request))

    async def acheck_tool_result(
        self, tool_name: str, result: Any, user_request: str | None = None
    ) -> Verdict:
        states = self._result_states(tool_name, result, user_request)
        blank = not _as_text(result).strip()
        return _worst([await self.guard._arun("input", s, blank, self.policy) for s in states])

    # --- context manager --------------------------------------------------------------------

    def __enter__(self) -> ToolGuard:
        self.guard.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.guard.__exit__(exc_type, exc, tb)
