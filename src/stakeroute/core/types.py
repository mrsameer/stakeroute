"""Frozen snapshot types and domain errors for the deterministic core.

Constitution Principle I (non-negotiable): this module has no I/O, no async,
no clock reads, no unseeded randomness, and no imports from
``stakeroute.storage`` or ``stakeroute.transport``. See
``tests/unit/test_core_purity.py`` for the mechanical enforcement.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


class DomainError(Exception):
    """Base class for all core domain errors. Never caught inside the core."""


class InvalidProbability(DomainError):
    """A probability fell outside the valid clamped range."""

    def __init__(self, value: float) -> None:
        self.value = value
        super().__init__(f"invalid probability: {value!r}")


class InvalidStake(DomainError):
    """A stake fell outside the configured per-forecast limits."""

    def __init__(self, value: int) -> None:
        self.value = value
        super().__init__(f"invalid stake: {value!r}")


class InsufficientCredits(DomainError):
    """An agent attempted to stake more than its available credits."""

    def __init__(self, available: int, requested: int) -> None:
        self.available = available
        self.requested = requested
        super().__init__(
            f"insufficient credits: available={available!r} requested={requested!r}"
        )


class EmptyCluster(DomainError):
    """An evidence cluster size below 1 was supplied."""

    def __init__(self, value: int) -> None:
        self.value = value
        super().__init__(f"invalid cluster size: {value!r}")


class InvalidOutcome(DomainError):
    """An outcome outside {0, 1} was supplied to a scoring function."""

    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__(f"invalid outcome: {value!r}")


PROBABILITY_FLOOR = 0.01
PROBABILITY_CEIL = 0.99


def clamp_probability(p: float) -> float:
    """Clamp ``p`` to ``[0.01, 0.99]`` so scoring stays well defined (D-009)."""
    if p < PROBABILITY_FLOOR:
        return PROBABILITY_FLOOR
    if p > PROBABILITY_CEIL:
        return PROBABILITY_CEIL
    return p


def compute_event_id(
    tenant_id: str, source: str, source_event_id: str, ts_ms: int
) -> str:
    """Compute the idempotency key for an ingested event (D-005).

    ``event_id = sha256(tenant_id|source|source_event_id|floor(ts_ms/1000))``.
    The one-second timestamp bucket collapses genuine re-emissions of the same
    observation while keeping distinct observations distinct.
    """
    bucket = ts_ms // 1000
    key = f"{tenant_id}|{source}|{source_event_id}|{bucket}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AgentSnapshot:
    """An agent's economic state at the moment a ranking pass reads it."""

    id: str
    reputation: float
    available_credits: int
    staked_credits: int
    attested: bool
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ForecastSnapshot:
    """An agent's staked probabilistic claim about a hypothesis."""

    id: str
    agent_id: str
    hypothesis_id: str
    probability: float
    stake: int
    evidence_cluster_id: str


@dataclass(frozen=True, slots=True)
class HypothesisSnapshot:
    """A candidate explanation under evaluation."""

    id: str
    statement: str
    prior_probability: float
    impact_minor_units: int
    urgency: float
    review_cost: float
    deadline_ms: int
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class Contribution:
    """One forecast's share of an aggregation result — the explanation."""

    agent_id: str
    forecast_id: str
    probability: float
    stake: int
    reputation: float
    evidence_cluster_id: str
    cluster_size: int
    independence: float
    weight: float
    alpha: float


@dataclass(frozen=True, slots=True)
class AggregationResult:
    """The aggregated probability, carrying its own explanation."""

    hypothesis_id: str
    probability: float
    is_prior: bool
    contributions: tuple[Contribution, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RankedHypothesis:
    """One hypothesis's position in a ranking pass, before allocation."""

    hypothesis_id: str
    probability: float
    priority: float
    impact_minor_units: int


@dataclass(frozen=True, slots=True)
class AllocationDecision:
    """Whether one hypothesis was routed, and why."""

    hypothesis_id: str
    rank: int
    routed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class AllocationResult:
    """The outcome of allocating a fixed budget across ranked hypotheses."""

    decisions: tuple[AllocationDecision, ...]
    withheld_count: int


@dataclass(frozen=True, slots=True)
class Settlement:
    """The economic consequence of scoring one forecast against an outcome."""

    forecast_id: str
    brier_score: float
    prior_brier_score: float
    improvement: float
    credit_delta: int
