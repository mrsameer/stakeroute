"""Influence weighting and probability aggregation (FR-013, FR-014, FR-016, FR-017)."""

from __future__ import annotations

import math
from collections import Counter

from stakeroute.core.independence import independence_factor
from stakeroute.core.types import (
    AgentSnapshot,
    AggregationResult,
    Contribution,
    ForecastSnapshot,
)


def influence_weight(reputation: float, stake: int, independence: float) -> float:
    """Return ``reputation * sqrt(stake) * independence``.

    Sub-linear in stake by construction (FR-014): doubling stake does not
    double influence, so a single well-capitalized agent cannot buy
    proportionally unbounded influence.
    """
    return reputation * math.sqrt(stake) * independence


def aggregate_probability(
    forecasts: tuple[ForecastSnapshot, ...],
    agents: dict[str, AgentSnapshot],
    prior_probability: float,
) -> AggregationResult:
    """Combine weighted forecasts into a single aggregated probability.

    Cluster sizes are computed live from the given forecast set (never
    incrementally maintained), so the result cannot drift under redelivery.
    Returns the hypothesis prior, unexplained by any contribution, when the
    forecast set is empty (FR-016).

    The explanation is part of the return value, not a separate call —
    Principle II is enforced by making the unexplained form unrepresentable.
    """
    if not forecasts:
        return AggregationResult(
            hypothesis_id="", probability=prior_probability, is_prior=True
        )

    hypothesis_id = forecasts[0].hypothesis_id
    cluster_sizes: Counter[str] = Counter(f.evidence_cluster_id for f in forecasts)
    ordered = sorted(forecasts, key=lambda f: f.agent_id)

    weighted: list[tuple[ForecastSnapshot, float, float]] = []
    for forecast in ordered:
        agent = agents[forecast.agent_id]
        cluster_size = cluster_sizes[forecast.evidence_cluster_id]
        independence = independence_factor(cluster_size)
        weight = influence_weight(agent.reputation, forecast.stake, independence)
        weighted.append((forecast, independence, weight))

    total_weight = sum(weight for _, _, weight in weighted)

    contributions: list[Contribution] = []
    aggregated = 0.0
    for forecast, independence, weight in weighted:
        agent = agents[forecast.agent_id]
        alpha = weight / total_weight if total_weight > 0 else 0.0
        aggregated += alpha * forecast.probability
        contributions.append(
            Contribution(
                agent_id=forecast.agent_id,
                forecast_id=forecast.id,
                probability=forecast.probability,
                stake=forecast.stake,
                reputation=agent.reputation,
                evidence_cluster_id=forecast.evidence_cluster_id,
                cluster_size=cluster_sizes[forecast.evidence_cluster_id],
                independence=independence,
                weight=weight,
                alpha=alpha,
            )
        )

    return AggregationResult(
        hypothesis_id=hypothesis_id,
        probability=aggregated,
        is_prior=False,
        contributions=tuple(contributions),
    )
