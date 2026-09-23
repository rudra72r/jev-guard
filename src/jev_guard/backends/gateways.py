"""Reach Jev through a hosting provider's gateway instead of a TypeSafe account.

TypeSafe's own signups have been closed at times since Jev launched. Cloudflare Workers AI
serves the same model, with the same request and response shapes, billed to a Cloudflare
account:

    export CLOUDFLARE_ACCOUNT_ID=...      # dash.cloudflare.com, right-hand sidebar
    export CLOUDFLARE_API_TOKEN=...       # token with Account > Workers AI > Read
    export JEV_GUARD_BACKEND=cloudflare

Everything else is unchanged: same policies, verdicts, CLI, and integrations.

Also serving Jev, but not usable from Python today:

- **Vercel AI Gateway** (`typesafe-ai/jev`) through the AI SDK's ``experimental_evaluate``
  in TypeScript.
- **Netlify AI Gateway**, which injects credentials for ``@typesafe-ai/sdk`` inside Netlify
  Functions only.

Checked against Cloudflare's model docs on 2026-09-23.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any

import httpx2

from jev_guard.backends.judge import _raise_for
from jev_guard.client import JevResult, WireQuestions
from jev_guard.errors import ConfigurationError, JevUnavailableError
from jev_guard.types import JevAnswer

CLOUDFLARE_MODEL = "typesafe/jev"
CLOUDFLARE_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run"


def answers_from_payload(payload: Mapping[str, Any]) -> dict[str, JevAnswer]:
    """Typed answers from Jev's JSON (the same shape TypeSafe's own API returns)."""
    answers: dict[str, JevAnswer] = {}
    for name, answer in (payload.get("answers") or {}).items():
        if not isinstance(answer, Mapping):
            continue
        kind = answer.get("type")
        if kind == "noul":
            answers[name] = JevAnswer(type="noul", noul=float(answer.get("noul", 0.0)))
        elif kind == "choice":
            probabilities = {
                str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()
            }
            answers[name] = JevAnswer(
                type="choice",
                choice=str(answer.get("choice", "")),
                confidence=float(answer.get("confidence", 0.0)),
                probabilities=probabilities or None,
            )
        elif kind == "score":
            probabilities = {
                str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()
            }
            answers[name] = JevAnswer(
                type="score",
                score=float(answer.get("score", 0.0)),
                confidence=float(answer.get("confidence", 0.0)),
                probabilities=probabilities or None,
            )
    return answers


class CloudflareJevBackend:
    """Jev through Cloudflare Workers AI. No TypeSafe account needed."""

    needs_typesafe_key = False
    provider = "Cloudflare Workers AI"

    def __init__(  # noqa: PLR0913 (keyword-only options)
        self,
        account_id: str | None = None,
        api_token: str | None = None,
        *,
        model: str = CLOUDFLARE_MODEL,
        price_per_million: float = 0.042,
        timeout: float = 60.0,
        transport: httpx2.BaseTransport | None = None,
        async_transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self.account_id = account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        self.api_token = (
            api_token if api_token is not None else os.environ.get("CLOUDFLARE_API_TOKEN", "")
        )
        self.model = model
        self.input_price_per_million = price_per_million  # Cloudflare bills this, not TypeSafe
        self._timeout = timeout
        self._transport = transport
        self._async_transport = async_transport
        self._client: httpx2.Client | None = None

    @property
    def name(self) -> str:
        return "cloudflare:jev"

    def _request(
        self, state: Mapping[str, Any], questions: WireQuestions
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        if not self.account_id or not self.api_token:
            raise ConfigurationError(
                "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are needed for the "
                "cloudflare backend.",
                hint="Create a token with Account > Workers AI > Read at dash.cloudflare.com.",
            )
        payload = {
            "model": self.model,
            "input": {
                "state": dict(state),
                "questions": {k: dict(v) for k, v in questions.items()},
            },
        }
        headers = {"Authorization": f"Bearer {self.api_token}"}
        return CLOUDFLARE_URL.format(account=self.account_id), headers, payload

    def _result(self, body: Mapping[str, Any], latency_ms: float) -> JevResult:
        # Cloudflare usually wraps responses in "result"; the model docs show it unwrapped.
        wrapped = body.get("result")
        payload: Mapping[str, Any] = wrapped if isinstance(wrapped, Mapping) else body
        usage = payload.get("usage") or {}
        tokens = usage.get("input_tokens")
        cost = (tokens or 0) * self.input_price_per_million / 1e6
        return JevResult(
            answers=answers_from_payload(payload),
            input_tokens=tokens,
            model=f"{payload.get('model', 'jev')} (cloudflare)",
            latency_ms=latency_ms,
            cost_usd=cost,
        )

    def evaluate(self, state: Mapping[str, Any], questions: WireQuestions) -> JevResult:
        url, headers, payload = self._request(state, questions)
        if self._client is None:
            self._client = httpx2.Client(timeout=self._timeout, transport=self._transport)
        start = time.perf_counter()
        try:
            response = self._client.post(url, json=payload, headers=headers)
        except (httpx2.TimeoutException, httpx2.TransportError) as err:
            raise JevUnavailableError(f"{self.provider} unreachable: {err}") from err
        _raise_for(response, self.provider)
        return self._result(response.json(), (time.perf_counter() - start) * 1000)

    async def aevaluate(self, state: Mapping[str, Any], questions: WireQuestions) -> JevResult:
        url, headers, payload = self._request(state, questions)
        start = time.perf_counter()
        async with httpx2.AsyncClient(
            timeout=self._timeout, transport=self._async_transport
        ) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
            except (httpx2.TimeoutException, httpx2.TransportError) as err:
                raise JevUnavailableError(f"{self.provider} unreachable: {err}") from err
        _raise_for(response, self.provider)
        return self._result(response.json(), (time.perf_counter() - start) * 1000)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
