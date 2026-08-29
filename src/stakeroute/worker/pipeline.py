"""The ranking pass — glues the pure core to storage.

This module is deliberately outside ``stakeroute.core``: it reads a
``Repository``, calls core functions, and writes ``attention_decisions``
rows. The core itself never sees a database handle (D-001).
"""

from __future__ import annotations

from dataclasses import dataclass

from stakeroute.core.baselines import (
    highest_confidence_probability,
    majority_vote_probability,
)
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

STRATEGIES = ("stakeroute", "majority_vote", "highest_confidence")


@dataclass(frozen=True, slots=True)
class RankingPassResult:
    """What one strategy's ranking pass produced, for the API layer to render."""

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


def _contributions_json(result: AggregationResult) -> list[dict]:
    return [
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


def _run_one_strategy(
    repo: Repository,
    tenant_id: str,
    strategy: str,
    hypotheses: list,
    agents: dict[str, AgentSnapshot],
    budget: int,
    decided_at_ms: int,
) -> RankingPassResult:
    ranked: list[RankedHypothesis] = []
    aggregates: dict[str, AggregationResult] = {}

    for hypothesis in hypotheses:
        forecasts = _forecast_snapshots(repo, hypothesis["id"])
        if strategy == "stakeroute":
            result = aggregate_probability(
                forecasts, agents, hypothesis["prior_probability"]
            )
        elif strategy == "majority_vote":
            probability = majority_vote_probability(
                forecasts, hypothesis["prior_probability"]
            )
            result = AggregationResult(
                hypothesis_id=hypothesis["id"],
                probability=probability,
                is_prior=not forecasts,
            )
        else:
            probability = highest_confidence_probability(
                forecasts, hypothesis["prior_probability"]
            )
            result = AggregationResult(
                hypothesis_id=hypothesis["id"],
                probability=probability,
                is_prior=not forecasts,
            )

        aggregates[hypothesis["id"]] = result
        if strategy == "stakeroute":
            # The considered mechanism folds business impact, urgency and
            # review cost into priority — that weighting is part of what
            # makes it more than a popularity contest.
            priority = priority_score(
                result.probability,
                hypothesis["impact_minor_units"],
                hypothesis["urgency"],
                hypothesis["review_cost"],
            )
        else:
            # A naive baseline has no such sophistication: it ranks by its
            # raw score alone. Folding in impact here would let a
            # deliberately high-impact true incident always win by
            # construction, regardless of how badly the baseline's own
            # probability estimate has been manipulated — masking exactly
            # the failure mode this comparison exists to expose (FR-023).
            priority = result.probability
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
        repo.insert_attention_decision(
            tenant_id=tenant_id,
            hypothesis_id=decision.hypothesis_id,
            strategy=strategy,
            aggregated_probability=result.probability,
            priority=ranked_by_id[decision.hypothesis_id].priority,
            rank=decision.rank,
            routed=decision.routed,
            reason=decision.reason,
            contributions=_contributions_json(result),
            decided_at_ms=decided_at_ms,
        )

    return RankingPassResult(
        allocation=allocation, aggregates=aggregates, ranked=tuple(ranked)
    )


def run_ranking_pass(
    repo: Repository,
    tenant_id: str,
    budget: int,
    decided_at_ms: int,
) -> dict[str, RankingPassResult]:
    """Run one ranking pass over every open hypothesis, under all three
    strategies (FR-023, FR-032).

    Cluster sizes are computed live from the current forecast set for each
    hypothesis — never incrementally maintained — so the result cannot
    drift under redelivery. Persists one ``attention_decisions`` row per
    hypothesis per strategy; the ``"stakeroute"`` rows carry the full
    per-forecast explanation as JSON (FR-017, FR-021), the baseline rows
    carry an empty contributions list since neither baseline explains
    itself by weight.

    Writing all three every pass over the identical event stream is what
    makes the side-by-side comparison (FR-023, FR-032) a stored fact rather
    than a UI trick.
    """
    hypotheses = repo.list_hypotheses(tenant_id, status="open")
    agents = _load_agent_snapshots(repo, tenant_id)

    results = {
        strategy: _run_one_strategy(
            repo, tenant_id, strategy, hypotheses, agents, budget, decided_at_ms
        )
        for strategy in STRATEGIES
    }
    repo.commit()
    return results
