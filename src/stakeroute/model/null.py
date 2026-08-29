"""No-model operation (FR-126, SC-113).

The system must start with no model configured at all and still run:
collectors ingest, ranking passes complete, settlement works. This client
is what every test that is not specifically about the model uses, and what
``STAKEROUTE_MODEL=none`` selects in real mode.
"""

from __future__ import annotations

from stakeroute.model.protocol import (
    CAPABILITY_HYPOTHESIS_PROPOSAL,
    CAPABILITY_PROSE_EXPLANATION,
    Accepted,
    ModelStateReport,
    Rejected,
)


class NullModelClient:
    """Always rejects with ``MODEL_DISABLED``; always reports
    ``state() == 'unconfigured'``."""

    def __init__(self) -> None:
        self._call_count = 0

    async def complete(
        self, purpose: str, prompt: str, timeout_s: float
    ) -> Accepted[dict] | Rejected:
        del purpose, prompt, timeout_s  # never consulted; no model is configured
        self._call_count += 1
        return Rejected(
            reason="MODEL_DISABLED",
            interaction_id=f"null-{self._call_count}",
            latency_ms=0,
            detail="no model configured (STAKEROUTE_MODEL=none)",
        )

    def state(self) -> ModelStateReport:
        return ModelStateReport(
            state="unconfigured",
            detail="no model configured",
            unavailable_capabilities=(
                CAPABILITY_HYPOTHESIS_PROPOSAL,
                CAPABILITY_PROSE_EXPLANATION,
            ),
            calls_this_interval=0,
            ceiling=0,
        )
