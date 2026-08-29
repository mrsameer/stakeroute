"""Tests for influence weighting and probability aggregation."""

import math

import pytest

from stakeroute.core.market import aggregate_probability, influence_weight
from stakeroute.core.types import AgentSnapshot, ForecastSnapshot


def _agent(agent_id: str, reputation: float = 0.8) -> AgentSnapshot:
    return AgentSnapshot(
        id=agent_id,
        reputation=reputation,
        available_credits=100,
        staked_credits=0,
        attested=True,
        created_at_ms=0,
    )


def _forecast(
    agent_id: str,
    hypothesis_id: str = "h-1",
    probability: float = 0.8,
    stake: int = 10,
    evidence_cluster_id: str = "cluster-a",
) -> ForecastSnapshot:
    return ForecastSnapshot(
        id=f"f-{agent_id}",
        agent_id=agent_id,
        hypothesis_id=hypothesis_id,
        probability=probability,
        stake=stake,
        evidence_cluster_id=evidence_cluster_id,
    )


def test_influence_weight_is_sub_linear_in_stake() -> None:
    low = influence_weight(reputation=0.8, stake=1, independence=1.0)
    high = influence_weight(reputation=0.8, stake=4, independence=1.0)
    # Stake quadrupled; weight should only double (sqrt), not quadruple.
    assert high == pytest.approx(low * 2.0)
    assert high < low * 4.0


def test_alpha_sums_to_one() -> None:
    agents = {"a1": _agent("a1"), "a2": _agent("a2"), "a3": _agent("a3")}
    forecasts = (
        _forecast("a1", evidence_cluster_id="c1"),
        _forecast("a2", evidence_cluster_id="c2"),
        _forecast("a3", evidence_cluster_id="c3"),
    )
    result = aggregate_probability(forecasts, agents, prior_probability=0.3)
    total_alpha = sum(c.alpha for c in result.contributions)
    assert total_alpha == pytest.approx(1.0)


def test_empty_forecast_set_returns_prior() -> None:
    result = aggregate_probability((), {}, prior_probability=0.42)
    assert result.is_prior is True
    assert result.probability == 0.42
    assert result.contributions == ()


def test_explanation_present_on_every_result() -> None:
    agents = {"a1": _agent("a1")}
    forecasts = (_forecast("a1"),)
    result = aggregate_probability(forecasts, agents, prior_probability=0.3)
    assert len(result.contributions) == 1
    c = result.contributions[0]
    assert c.agent_id == "a1"
    assert c.alpha == pytest.approx(1.0)
    assert c.weight > 0


def test_correlated_forecasts_discounted_relative_to_independent() -> None:
    agents = {f"a{i}": _agent(f"a{i}") for i in range(1, 4)}
    independent = (
        _forecast("a1", evidence_cluster_id="c1"),
        _forecast("a2", evidence_cluster_id="c2"),
        _forecast("a3", evidence_cluster_id="c3"),
    )
    correlated = (
        _forecast("a1", evidence_cluster_id="shared"),
        _forecast("a2", evidence_cluster_id="shared"),
        _forecast("a3", evidence_cluster_id="shared"),
    )
    independent_result = aggregate_probability(
        independent, agents, prior_probability=0.3
    )
    correlated_result = aggregate_probability(correlated, agents, prior_probability=0.3)
    independent_total_weight = sum(c.weight for c in independent_result.contributions)
    correlated_total_weight = sum(c.weight for c in correlated_result.contributions)
    assert correlated_total_weight == pytest.approx(
        independent_total_weight / math.sqrt(3)
    )
