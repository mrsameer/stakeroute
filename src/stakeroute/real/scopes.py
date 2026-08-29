"""Declared evidence access scopes and the evidence bundle (D-013, FR-113, FR-114).

The exclusion in FR-113 is enforced by the type, not by a convention:
``EvidenceBundle`` has no field that could carry an outcome, a ground-truth
label or an accuracy parameter. ``frozen=True`` means an agent cannot
mutate what it was given.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from stakeroute.core.types import ObservationSnapshot
from stakeroute.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class EvidenceAccessScope:
    """The only input to bundle construction. Declared in configuration —
    an agent's scope is not something it can choose or expand for itself."""

    agent_id: str
    source_ids: frozenset[str]
    label: str


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """Exactly what an agent sees before it forecasts, and nothing else.

    **The critical property**: no field here could carry an outcome, a
    ground-truth label or an accuracy parameter. SC-102 is verified over
    the recorded serialization of this type — a query, not a code review.
    """

    agent_id: str
    hypothesis_id: str
    hypothesis_statement: str
    scope: EvidenceAccessScope
    observations: tuple[ObservationSnapshot, ...]
    built_at_ms: int


@dataclass(frozen=True, slots=True)
class ForecastProposal:
    """What a reasoning agent returns, before it becomes a ``forecasts``
    row."""

    agent_id: str
    hypothesis_id: str
    probability: float
    stake: int
    evidence_cluster_id: str
    rationale: str
    interaction_id: str


def build_evidence_bundle(
    repo: Repository,
    tenant_id: str,
    scope: EvidenceAccessScope,
    hypothesis_id: str,
    hypothesis_statement: str,
    window_start_ms: int,
    window_end_ms: int,
    built_at_ms: int,
) -> EvidenceBundle:
    """The sole construction path for an ``EvidenceBundle`` (D-013).

    Queries only sources inside ``scope.source_ids`` — this is the one
    function whose signature a reviewer needs to read to check that an
    agent cannot reach evidence outside its declared scope.
    """
    observations = tuple(
        ObservationSnapshot(
            event_id=row["event_id"],
            source=row["source"],
            observed_at_ms=row["observed_at_ms"],
            payload=json.loads(row["payload"]),
            severity=0.0,  # not persisted; irrelevant to reasoning, only to estimation
        )
        for row in repo.list_events(
            tenant_id, since_ms=window_start_ms, until_ms=window_end_ms
        )
        if row["source"] in scope.source_ids
    )
    return EvidenceBundle(
        agent_id=scope.agent_id,
        hypothesis_id=hypothesis_id,
        hypothesis_statement=hypothesis_statement,
        scope=scope,
        observations=observations,
        built_at_ms=built_at_ms,
    )


def serialize_evidence_bundle(bundle: EvidenceBundle) -> dict:
    """The exact bundle handed to the agent, as recorded on the resulting
    ``forecasts`` row (FR-113, FR-115). Storing this is the difference
    between an enforced exclusion and a claimed one."""
    return {
        "agent_id": bundle.agent_id,
        "hypothesis_id": bundle.hypothesis_id,
        "hypothesis_statement": bundle.hypothesis_statement,
        "scope": {
            "agent_id": bundle.scope.agent_id,
            "source_ids": sorted(bundle.scope.source_ids),
            "label": bundle.scope.label,
        },
        "observations": [
            {
                "event_id": o.event_id,
                "source": o.source,
                "observed_at_ms": o.observed_at_ms,
                "payload": o.payload,
            }
            for o in bundle.observations
        ],
        "built_at_ms": bundle.built_at_ms,
    }
