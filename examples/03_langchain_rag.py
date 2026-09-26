"""Example 03: grounding checks on a LangChain RAG chain.

    pip install "jev-guard[langchain] @ git+https://github.com/rudra72r/jev-guard"
    pip install langchain-anthropic
    export TYPESAFE_API_KEY=sk-...    # https://console.typesafe.ai/keys
    export ANTHROPIC_API_KEY=sk-ant-...
    python examples/03_langchain_rag.py

The callback handler sees the retrieved documents and passes them to the rag policy as
`context`, so answers that aren't backed by the documents get flagged or blocked. The
retriever is a tiny keyword matcher so the example needs no vector database.
"""

from __future__ import annotations

import os

from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import RunnablePassthrough

from jev_guard.errors import GuardBlockedError
from jev_guard.integrations.langchain_cb import JevGuardCallbackHandler

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

DOCUMENTS = [
    Document(page_content="International wire transfers cost $25 and settle in 1-3 business days."),
    Document(page_content="Domestic ACH transfers are free and settle in 1-2 business days."),
    Document(page_content="Daily ATM withdrawal limit is $1,000 for standard checking accounts."),
    Document(page_content="Overdraft fee is $30, waived once per calendar year on request."),
]


class KeywordRetriever(BaseRetriever):
    """Returns the documents sharing the most words with the query."""

    documents: list[Document]
    k: int = 2

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        words = set(query.lower().split())
        ranked = sorted(
            self.documents, key=lambda d: -len(words & set(d.page_content.lower().split()))
        )
        return ranked[: self.k]


def format_docs(docs: list[Document]) -> str:
    return "\n".join(f"[doc {i}] {d.page_content}" for i, d in enumerate(docs, 1))


prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Answer using only the documents below and cite them like [doc 1]. If they don't "
            "contain the answer, say so.\n\n{context}",
        ),
        ("human", "{question}"),
    ]
)
chain = (
    {
        "context": KeywordRetriever(documents=DOCUMENTS) | format_docs,
        "question": RunnablePassthrough(),
    }
    | prompt
    | ChatAnthropic(model=MODEL, max_tokens=300)
    | StrOutputParser()
)


def ask(question: str) -> str:
    handler = JevGuardCallbackHandler(policy="rag")  # one handler per request
    try:
        answer = chain.invoke(question, config={"callbacks": [handler]})
    except GuardBlockedError as blocked:
        return f"{blocked.suggested_response}  [blocked: {'; '.join(blocked.verdict.reasons)}]"
    output_verdict = handler.verdicts[-1]
    note = f"  [{output_verdict.action}, confidence {output_verdict.confidence:.2f}]"
    return answer + note


if __name__ == "__main__":
    for question in [
        "How much does an international wire transfer cost?",
        "What is the interest rate on a 12-month certificate of deposit?",
    ]:
        print(f"> {question}\n{ask(question)}\n")
