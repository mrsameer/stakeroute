"""The ranking pass — glues the pure core to storage.

This module is deliberately outside ``stakeroute.core``: it reads a
``Repository``, calls core functions, and writes ``attention_decisions``
rows. The core itself never sees a database handle (D-001).
"""

from __future__ import annotations

from dataclasses import dataclass

from stakeroute.core.market import aggregate_probability
from stakeroute.core.ranking import allocate_attention, priority_score
from stakeroute.core.types import (
    AgentSnapshot,
    AggregationResult,
    AllocationResult,
    ForecastSnapshot,
    RankedHypothesis,
)
from stakeroute.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class RankingPassResult:
    """What one ranking pass produced, for the caller (API layer) to render."""

    allocation: AllocationResult
    aggregates: dict[str, AggregationResult]
    ranked: tuple[RankedHypothesis, ...]


def _load_agent_snapshots(repo: Repository, tenant_id: str) -> dict[str, AgentSnapshot]:
    return {
        row["id"]: AgentSnapshot(
            id=row["id"],
            reputation=row["reputation"],
            available_credits=row["available_credits"],
            staked_credits=row["staked_credits"],
            attested=bool(row["attested"]),
            created_at_ms=row["created_at_ms"],
        )
        for row in repo.list_agents(tenant_id)
    }


def _forecast_snapshots(
    repo: Repository, hypothesis_id: str
) -> tuple[ForecastSnapshot, ...]:
    return tuple(
        ForecastSnapshot(
            id=row["id"],
            agent_id=row["agent_id"],
            hypothesis_id=row["hypothesis_id"],
            probability=row["probability"],
            stake=row["stake"],
            evidence_cluster_id=row["evidence_cluster_id"],
        )
        for row in repo.list_forecasts_for_hypothesis(hypothesis_id)
    )


def run_ranking_pass(
    repo: Repository,
    tenant_id: str,
    budget: int,
    decided_at_ms: int,
) -> RankingPassResult:
    """Run one StakeRoute ranking pass over every open hypothesis.

    Cluster sizes are computed live from the current forecast set for each
    hypothesis — never incrementally maintained — so the result cannot
    drift under redelivery. Persists one ``attention_decisions`` row per
    hypothesis under the ``"stakeroute"`` strategy, each carrying its full
    per-forecast explanation as JSON (FR-017, FR-021).
    """
    hypotheses = repo.list_hypotheses(tenant_id, status="open")
    agents = _load_agent_snapshots(repo, tenant_id)

    ranked: list[RankedHypothesis] = []
    aggregates: dict[str, AggregationResult] = {}
    for hypothesis in hypotheses:
        forecasts = _forecast_snapshots(repo, hypothesis["id"])
        result = aggregate_probability(
            forecasts, agents, hypothesis["prior_probability"]
        )
        aggregates[hypothesis["id"]] = result
        priority = priority_score(
            result.probability,
            hypothesis["impact_minor_units"],
            hypothesis["urgency"],
            hypothesis["review_cost"],
        )
        ranked.append(
            RankedHypothesis(
                hypothesis_id=hypothesis["id"],
                probability=result.probability,
                priority=priority,
                impact_minor_units=hypothesis["impact_minor_units"],
            )
        )

    allocation = allocate_attention(tuple(ranked), budget)
    ranked_by_id = {r.hypothesis_id: r for r in ranked}

    for decision in allocation.decisions:
        result = aggregates[decision.hypothesis_id]
        contributions = [
            {
                "agent_id": c.agent_id,
                "forecast_id": c.forecast_id,
                "probability": c.probability,
                "stake": c.stake,
                "reputation": c.reputation,
                "evidence_cluster_id": c.evidence_cluster_id,
                "cluster_size": c.cluster_size,
                "independence": c.independence,
                "weight": c.weight,
                "alpha": c.alpha,
            }
            for c in result.contributions
        ]
        repo.insert_attention_decision(
            tenant_id=tenant_id,
            hypothesis_id=decision.hypothesis_id,
            strategy="stakeroute",
            aggregated_probability=result.probability,
            priority=ranked_by_id[decision.hypothesis_id].priority,
            rank=decision.rank,
            routed=decision.routed,
            reason=decision.reason,
            contributions=contributions,
            decided_at_ms=decided_at_ms,
        )
    repo.commit()

    return RankingPassResult(
        allocation=allocation, aggregates=aggregates, ranked=tuple(ranked)
    )
