"""LangChain integration: a callback handler that checks every LLM call in a chain or agent.

    from jev_guard.integrations.langchain_cb import JevGuardCallbackHandler

    handler = JevGuardCallbackHandler(policy="rag")
    chain.invoke({"question": q}, config={"callbacks": [handler]})  # GuardBlockedError on block

Input is checked when a model starts (the last human message), output when it ends.
Documents from a retriever in the same run are passed as ``context``, so the ``rag``
policy's grounding checks work with no extra wiring. Use one handler per request: it keeps
the latest retrieved documents between callbacks.

Requires ``pip install jev-guard[langchain]``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.documents import Document
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

from jev_guard.guard import Guard
from jev_guard.integrations._common import enforce, text_of
from jev_guard.policies.base import Policy
from jev_guard.types import Verdict

__all__ = ["JevGuardCallbackHandler"]


class JevGuardCallbackHandler(BaseCallbackHandler):
    """Runs jev-guard checks around every LLM call. Blocked checks raise ``GuardBlockedError``.

    ``verdicts`` collects every verdict from this handler, in order, for logging or audit.
    """

    raise_error: bool = True  # let GuardBlockedError stop the chain instead of being logged

    def __init__(self, policy: str | Policy | None = None) -> None:
        super().__init__()
        self.guard = Guard(policy=policy)
        self.verdicts: list[Verdict] = []
        self._prompts: dict[UUID, str] = {}
        self._context: list[str] | None = None

    def _check_input(self, run_id: UUID, prompt: str) -> None:
        self._prompts[run_id] = prompt
        self.verdicts.append(enforce(self.guard.check_input(prompt)))

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        prompt = ""
        for message in reversed(messages[0] if messages else []):
            if message.type == "human":
                prompt = text_of(message.content)
                break
        self._check_input(run_id, prompt)

    def on_llm_start(
        self, serialized: dict[str, Any], prompts: list[str], *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._check_input(run_id, prompts[-1] if prompts else "")

    def on_retriever_end(
        self, documents: Sequence[Document], *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._context = [doc.page_content for doc in documents]

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        prompt = self._prompts.pop(run_id, "")
        generations = response.generations[0] if response.generations else []
        reply = generations[0].text if generations else ""
        verdict = self.guard.check_output(prompt, reply, context=self._context)
        self.verdicts.append(enforce(verdict))

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._prompts.pop(run_id, None)
