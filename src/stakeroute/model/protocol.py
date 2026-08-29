"""The ``ModelClient`` Protocol and result types (contracts/model-boundary.md).

One rule governs this whole boundary: *the model produces prose and
selections; it never produces a number that anything reads.* Everything
here is machinery for enforcing that and for surviving the model's
absence (Constitution Principle I, D-011, D-020).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

ModelState = Literal["ok", "degraded", "ceiling_reached", "disabled", "unconfigured"]

RejectionReason = Literal[
    "MALFORMED_SHAPE",
    "NO_CITATIONS",
    "UNKNOWN_CITATION",
    "CITATION_OUT_OF_WINDOW",
    "UNKNOWN_CONDITION",
    "INVALID_CONDITION_PARAMS",
    "PROBABILITY_OUT_OF_RANGE",
    "STAKE_OUT_OF_RANGE",
    "INSUFFICIENT_CREDITS",
    "EVIDENCE_SCOPE_VIOLATION",
    "REFUSAL",
    "MODEL_DISABLED",
    "TIMEOUT",
    "TRANSPORT_FAILURE",
    "CEILING_REACHED",
]

# The only two capabilities the model boundary can take away. Ranking and
# settlement are never on this list — they never call the model at all
# (FR-124).
CAPABILITY_HYPOTHESIS_PROPOSAL = "hypothesis_proposal"
CAPABILITY_PROSE_EXPLANATION = "prose_explanation"


@dataclass(frozen=True, slots=True)
class Accepted[T]:
    """A validated model result. ``interaction_id`` points at the durable
    ``model_interactions`` row recorded before this value was used."""

    value: T
    interaction_id: str
    latency_ms: int


@dataclass(frozen=True, slots=True)
class Rejected:
    """A rejection a caller cannot silently ignore — ``ModelResult`` has no
    third, unhandled case."""

    reason: RejectionReason
    interaction_id: str
    latency_ms: int
    detail: str = ""


type ModelResult[T] = Accepted[T] | Rejected


@dataclass(frozen=True, slots=True)
class ModelStateReport:
    """What ``ModelClient.state()`` returns — the operator surface's whole
    picture of the model boundary (FR-124, FR-125)."""

    state: ModelState
    detail: str
    unavailable_capabilities: tuple[str, ...]
    calls_this_interval: int
    ceiling: int


@dataclass(frozen=True, slots=True)
class ProposalDraft:
    """A validated ``purpose='proposal'`` response, before it is priced."""

    statement: str
    cited_observation_ids: tuple[str, ...]
    condition_name: str | None
    condition_params: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class ForecastDraft:
    """A validated ``purpose='forecast'`` response, before the probability
    is clamped for storage (feature 001 D-009)."""

    probability: float
    stake: int
    rationale: str


@runtime_checkable
class ModelClient(Protocol):
    """Every caller depends on this Protocol, never on a concrete SDK
    (D-011). A caller that forgets to handle ``Rejected`` gets a type
    error, not a silent ``None``."""

    async def complete(
        self, purpose: str, prompt: str, timeout_s: float
    ) -> Accepted[dict] | Rejected:
        """Return the model's raw parsed JSON response, or a rejection.

        Never raises for a model-side failure — a timeout or transport
        failure is returned as ``Rejected(reason='TIMEOUT' |
        'TRANSPORT_FAILURE', ...)``, not an exception. Full schema and
        business-rule validation (citations, ranges, scope) happens in
        ``model/validation.py`` against the returned value — this method
        only guarantees a durably recorded interaction and a well-formed
        result type.
        """
        ...

    def state(self) -> ModelStateReport:
        """Current capability state, for the operator surface (FR-124)."""
        ...
