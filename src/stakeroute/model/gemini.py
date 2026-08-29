"""Vertex AI adapter (D-011). Nothing outside this module imports
``google.genai`` — every caller depends on the ``ModelClient`` Protocol.

The adapter never places a token in a prompt or a log line (FR-127,
SC-112): authentication is handled entirely by the SDK's own credential
loading from the path in ``GOOGLE_APPLICATION_CREDENTIALS``, which this
module never reads the contents of.
"""

from __future__ import annotations

import asyncio
import json
import time

from google import genai

from stakeroute.model.budget import (
    ModelBudget,
    capability_for_purpose,
    compute_model_state,
)
from stakeroute.model.protocol import Accepted, ModelStateReport, Rejected


class GeminiClient:
    """A live Vertex AI ``ModelClient``, with a per-request timeout
    (FR-121) and a call ceiling enforced before any request is made
    (D-020)."""

    def __init__(
        self,
        model_name: str,
        project: str,
        location: str,
        budget: ModelBudget,
    ) -> None:
        self._model_name = model_name
        self._budget = budget
        self._client = genai.Client(vertexai=True, project=project, location=location)
        self._consecutive_failures = 0

    async def complete(
        self, purpose: str, prompt: str, timeout_s: float
    ) -> Accepted[dict] | Rejected:
        now_ms = int(time.time() * 1000)
        if not self._budget.has_capacity(now_ms):
            return Rejected(
                reason="CEILING_REACHED",
                interaction_id="",
                latency_ms=0,
                detail=f"ceiling reached for {capability_for_purpose(purpose)}",
            )

        started = time.monotonic()
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self._client.models.generate_content,
                    model=self._model_name,
                    contents=prompt,
                ),
                timeout=timeout_s,
            )
        except TimeoutError:
            latency_ms = int((time.monotonic() - started) * 1000)
            self._consecutive_failures += 1
            return Rejected(reason="TIMEOUT", interaction_id="", latency_ms=latency_ms)
        except Exception as exc:  # noqa: BLE001 - never raise for a model-side failure
            latency_ms = int((time.monotonic() - started) * 1000)
            self._consecutive_failures += 1
            return Rejected(
                reason="TRANSPORT_FAILURE",
                interaction_id="",
                latency_ms=latency_ms,
                detail=str(exc)[:200],
            )

        latency_ms = int((time.monotonic() - started) * 1000)
        self._budget.record_call(now_ms)
        self._consecutive_failures = 0

        text = getattr(response, "text", None)
        if not text:
            return Rejected(
                reason="MALFORMED_SHAPE", interaction_id="", latency_ms=latency_ms
            )
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            return Rejected(
                reason="MALFORMED_SHAPE", interaction_id="", latency_ms=latency_ms
            )
        if not isinstance(value, dict):
            return Rejected(
                reason="MALFORMED_SHAPE", interaction_id="", latency_ms=latency_ms
            )

        return Accepted(value=value, interaction_id="", latency_ms=latency_ms)

    def state(self) -> ModelStateReport:
        now_ms = int(time.time() * 1000)
        calls = self._budget.calls_this_interval(now_ms)
        ceiling = self._budget.ceiling()
        state, detail, unavailable = compute_model_state(
            calls, ceiling, self._consecutive_failures
        )
        return ModelStateReport(
            state=state,
            detail=detail,
            unavailable_capabilities=unavailable,
            calls_this_interval=calls,
            ceiling=ceiling,
        )
