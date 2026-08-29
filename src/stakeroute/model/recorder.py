"""The durable interaction log (FR-123, FR-128).

``ModelInteractionRecorder`` wraps a raw ``ModelClient`` and writes exactly
one ``model_interactions`` row — before its result is used by any caller —
for every call, including timeouts, transport failures, and every business
rejection reason validation can produce (FR-122). Centralizing validation
here (via the caller-supplied ``validate`` callback) is what makes "every
rejection reason produces a row with ``accepted = 0``" hold for citation,
condition, range, scope and credit failures, not just transport-level ones.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import time
from collections.abc import Callable

from stakeroute.model.protocol import Accepted, ModelClient, Rejected, RejectionReason
from stakeroute.storage.repository import Repository


def compute_interaction_id(
    tenant_id: str,
    purpose: str,
    prompt: str,
    requested_at_ms: int,
    call_seq: int = 0,
) -> str:
    """``sha256(tenant|purpose|request_hash|requested_at_ms|call_seq)``
    (data-model.md), mirroring ``core/types.py::compute_event_id``'s
    pattern for events.

    ``call_seq`` disambiguates two distinct calls that land in the same
    millisecond with byte-identical prompts — a real occurrence when an
    unchanged observation window is proposed over twice in quick
    succession, not only a test-speed artifact. Millisecond timestamps
    alone are not fine-grained enough to keep such calls distinct.
    """
    request_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    key = f"{tenant_id}|{purpose}|{request_hash}|{requested_at_ms}|{call_seq}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ModelInteractionRecorder:
    """Every real-mode caller talks to the model through this wrapper, never
    to a raw ``ModelClient`` directly — that is what makes "every
    interaction recorded" (FR-123) a structural property rather than a
    matter of every call site remembering to log."""

    def __init__(
        self,
        repo: Repository,
        client: ModelClient,
        tenant_id: str,
        mode: str,
        model_name: str,
    ) -> None:
        self._repo = repo
        self._client = client
        self._tenant_id = tenant_id
        self._mode = mode
        self._model_name = model_name
        self._call_seq = itertools.count()

    async def complete[T](
        self,
        purpose: str,
        prompt: str,
        timeout_s: float,
        validate: Callable[[dict], T | RejectionReason],
        agent_id: str | None = None,
    ) -> Accepted[T] | Rejected:
        """Call the wrapped client, then run ``validate`` over any raw
        value it returns, and record exactly one row reflecting the final
        verdict — never the raw client's shape-only check alone.

        ``validate`` returns either the validated value or a
        ``RejectionReason`` string; it must not raise.
        """
        requested_at_ms = int(time.time() * 1000)
        call_seq = next(self._call_seq)
        raw_result = await self._client.complete(purpose, prompt, timeout_s)
        interaction_id = compute_interaction_id(
            self._tenant_id, purpose, prompt, requested_at_ms, call_seq
        )

        if isinstance(raw_result, Rejected):
            accepted = False
            rejection_reason: RejectionReason = raw_result.reason
            response_json = None
            value: T | None = None
            detail = raw_result.detail
        else:
            validated = validate(raw_result.value)
            response_json = json.dumps(raw_result.value)
            if isinstance(validated, str):
                accepted = False
                rejection_reason = validated  # type: ignore[assignment]
                value = None
                detail = ""
            else:
                accepted = True
                rejection_reason = None  # type: ignore[assignment]
                value = validated
                detail = ""

        inserted = self._repo.insert_model_interaction(
            interaction_id=interaction_id,
            tenant_id=self._tenant_id,
            mode=self._mode,
            purpose=purpose,
            agent_id=agent_id,
            request=prompt,
            response=response_json,
            latency_ms=raw_result.latency_ms,
            accepted=accepted,
            rejection_reason=None if accepted else rejection_reason,
            model_name=self._model_name,
            requested_at_ms=requested_at_ms,
        )
        self._repo.commit()

        if not inserted:
            # id collision: the same (tenant, purpose, request, millisecond)
            # was already recorded — most often a genuinely identical prompt
            # issued twice within the same millisecond, not a fresh call.
            # Re-derive the verdict from what is actually stored rather than
            # trusting this call's local computation, so two callers racing
            # on the same interaction never observe different outcomes for
            # the same recorded row.
            existing_row = self._repo.get_model_interaction(interaction_id)
            assert existing_row is not None
            if existing_row["accepted"]:
                existing_value = validate(json.loads(existing_row["response"]))
                if not isinstance(existing_value, str):
                    return Accepted(
                        value=existing_value,
                        interaction_id=interaction_id,
                        latency_ms=existing_row["latency_ms"],
                    )
                return Rejected(
                    reason=existing_value,  # type: ignore[arg-type]
                    interaction_id=interaction_id,
                    latency_ms=existing_row["latency_ms"],
                )
            return Rejected(
                reason=existing_row["rejection_reason"],
                interaction_id=interaction_id,
                latency_ms=existing_row["latency_ms"],
            )

        if accepted:
            return Accepted(
                value=value,  # type: ignore[arg-type]
                interaction_id=interaction_id,
                latency_ms=raw_result.latency_ms,
            )
        return Rejected(
            reason=rejection_reason,
            interaction_id=interaction_id,
            latency_ms=raw_result.latency_ms,
            detail=detail,
        )

    def state(self):
        return self._client.state()
