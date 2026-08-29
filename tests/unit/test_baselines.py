"""Each baseline must exhibit its intended failure mode.

A baseline that does not break under attack is a broken baseline — these
tests assert the failure, not correctness.
"""

from stakeroute.core.baselines import (
    highest_confidence_probability,
    majority_vote_probability,
)
from stakeroute.core.market import aggregate_probability
from stakeroute.core.types import AgentSnapshot, ForecastSnapshot


def _forecast(agent_id: str, probability: float, stake: int = 20) -> ForecastSnapshot:
    return ForecastSnapshot(
        id=f"f-{agent_id}",
        agent_id=agent_id,
        hypothesis_id="h-1",
        probability=probability,
        stake=stake,
        evidence_cluster_id=f"{agent_id}-cluster",
    )


def test_majority_vote_flips_under_sybil_flood() -> None:
    # A few honest, low-confidence "false" votes vs a flood of new Sybils
    # confidently asserting "true".
    honest = [_forecast(f"honest-{i}", 0.4) for i in range(3)]
    sybils = [_forecast(f"sybil-{i}", 0.9) for i in range(50)]
    probability = majority_vote_probability(
        tuple(honest + sybils), prior_probability=0.3
    )
    assert probability > 0.5  # the flood wins: one vote per identity


def test_majority_vote_empty_returns_prior() -> None:
    assert majority_vote_probability((), prior_probability=0.42) == 0.42


def test_highest_confidence_overridden_by_one_wrong_agent() -> None:
    honest = [_forecast(f"honest-{i}", 0.2) for i in range(5)]
    liar = [_forecast("liar", 0.98)]
    probability = highest_confidence_probability(
        tuple(honest + liar), prior_probability=0.3
    )
    assert probability == 0.98  # one confidently-wrong forecast dominates


def test_highest_confidence_empty_returns_prior() -> None:
    assert highest_confidence_probability((), prior_probability=0.42) == 0.42


def test_stakeroute_dampens_the_same_sybil_flood_far_more_than_majority_vote() -> None:
    # Identical population fed through both strategies: 3 honest agents at
    # 0.4 (correctly rejecting the hypothesis) vs 50 new, low-reputation
    # Sybils at 0.9. Reputation weighting alone (no evidence-cluster
    # discount, deliberately, to isolate its effect) cannot fully cancel 50
    # identities against 3 — but it must move the aggregate far less than
    # an unweighted vote does, which is the property SC-002 exploits at
    # full scenario scale.
    agents = {
        f"honest-{i}": AgentSnapshot(f"honest-{i}", 0.8, 100, 0, True, 0)
        for i in range(3)
    }
    agents.update(
        {
            f"sybil-{i}": AgentSnapshot(f"sybil-{i}", 0.1, 100, 0, False, 0)
            for i in range(50)
        }
    )
    honest = [_forecast(f"honest-{i}", 0.4, stake=30) for i in range(3)]
    sybils = [_forecast(f"sybil-{i}", 0.9, stake=5) for i in range(50)]
    all_forecasts = tuple(honest + sybils)

    stakeroute_result = aggregate_probability(
        all_forecasts, agents, prior_probability=0.3
    )
    majority_result = majority_vote_probability(all_forecasts, prior_probability=0.3)

    honest_only_truth = 0.4
    stakeroute_shift = abs(stakeroute_result.probability - honest_only_truth)
    majority_shift = abs(majority_result - honest_only_truth)

    assert majority_result > 0.5  # the flood flips the naive vote outright
    assert (
        stakeroute_shift < majority_shift / 2
    )  # weighting cuts the shift substantially
