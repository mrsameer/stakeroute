"""The cost-of-attack closed form must agree with the actual mechanism.

Every threshold in ``stakeroute.core.attack`` is derived by inverting
``market.aggregate_probability`` and ``ranking.priority_score``. A closed
form that has quietly drifted from the code it inverts is worse than no
closed form, so these tests do not check the algebra against itself: they
build the forecast population the formula prescribes, push it through the
real aggregation and ranking functions, and assert the attack actually
lands.
"""

import math

import pytest

from stakeroute.core.attack import (
    AttackCost,
    InfeasibleAttack,
    attack_frontier,
    baseline_attack_cost,
    highest_confidence_identities_required,
    majority_vote_identities_required,
    required_adversary_weight,
    required_probability,
    stakeroute_attack_cost,
    sybil_identities_required,
)
from stakeroute.core.baselines import majority_vote_probability
from stakeroute.core.market import aggregate_probability, influence_weight
from stakeroute.core.ranking import priority_score
from stakeroute.core.types import PROBABILITY_CEIL, AgentSnapshot, ForecastSnapshot

DEFENDER_PROBABILITY = 0.65
DEFENDER_IMPACT = 800_000_000
DEFENDER_PRIORITY = priority_score(DEFENDER_PROBABILITY, DEFENDER_IMPACT, 1.0, 1.0)

# A target of equal impact and urgency isolates the economic defence from
# the impact weighting: the only thing standing between the adversary and
# rank 1 is influence weight it has to buy.
TARGET_IMPACT = DEFENDER_IMPACT
TARGET_URGENCY = 1.0
TARGET_REVIEW_COST = 1.0


def _agent(agent_id: str, reputation: float) -> AgentSnapshot:
    return AgentSnapshot(
        id=agent_id,
        reputation=reputation,
        available_credits=100,
        staked_credits=0,
        attested=True,
        created_at_ms=0,
    )


def _forecast(
    forecast_id: str, agent_id: str, probability: float, stake: int, cluster: str
) -> ForecastSnapshot:
    return ForecastSnapshot(
        id=forecast_id,
        agent_id=agent_id,
        hypothesis_id="h-target",
        probability=probability,
        stake=stake,
        evidence_cluster_id=cluster,
    )


def test_required_probability_inverts_priority_score() -> None:
    threshold = required_probability(
        DEFENDER_PRIORITY, TARGET_IMPACT, TARGET_URGENCY, TARGET_REVIEW_COST
    )
    recovered = priority_score(
        threshold, TARGET_IMPACT, TARGET_URGENCY, TARGET_REVIEW_COST
    )
    assert recovered == pytest.approx(DEFENDER_PRIORITY)


def test_required_probability_rejects_degenerate_hypotheses() -> None:
    with pytest.raises(InfeasibleAttack):
        required_probability(1.0, TARGET_IMPACT, TARGET_URGENCY, 0.0)
    with pytest.raises(InfeasibleAttack):
        required_probability(1.0, 0, TARGET_URGENCY, 1.0)


def test_required_weight_lands_exactly_on_the_threshold() -> None:
    """The returned weight must move the real weighted mean to the target."""
    honest_probability = 0.20
    honest_weight = 4.0
    target = 0.60

    weight = required_adversary_weight(
        honest_weight, honest_probability, target, PROBABILITY_CEIL
    )
    assert weight is not None

    mean = (honest_weight * honest_probability + weight * PROBABILITY_CEIL) / (
        honest_weight + weight
    )
    assert mean == pytest.approx(target)


def test_required_weight_is_zero_when_already_above_threshold() -> None:
    assert required_adversary_weight(4.0, 0.7, 0.6) == 0.0


def test_required_weight_is_none_when_ceiling_cannot_reach_threshold() -> None:
    assert required_adversary_weight(4.0, 0.2, 0.995) is None


def test_identity_count_reproduces_the_weight_with_distinct_clusters() -> None:
    identities = sybil_identities_required(
        weight=6.0, reputation_per_identity=0.1, stake_per_identity=25
    )
    assert identities == 12  # 6 / (0.1 * 5) = 12

    mustered = sum(influence_weight(0.1, 25, 1.0) for _ in range(identities))
    assert mustered >= 6.0


def test_shared_evidence_squares_the_identity_count() -> None:
    """The independence discount turns a linear bill into a quadratic one."""
    distinct = sybil_identities_required(6.0, 0.1, 25, shared_evidence_cluster=False)
    shared = sybil_identities_required(6.0, 0.1, 25, shared_evidence_cluster=True)
    assert distinct == 12
    assert shared == 144

    # And the squared count really does muster the weight once every
    # identity is discounted by 1/sqrt(N).
    independence = 1.0 / math.sqrt(shared)
    mustered = shared * influence_weight(0.1, 25, independence)
    assert mustered >= 6.0


def test_identity_count_is_none_without_reputation_or_stake() -> None:
    assert sybil_identities_required(6.0, 0.0, 25) is None
    assert sybil_identities_required(6.0, 0.1, 0) is None


def test_zero_weight_requirement_needs_no_identities() -> None:
    assert sybil_identities_required(0.0, 0.1, 25) == 0


def test_stakeroute_attack_cost_actually_flips_the_ranking() -> None:
    """End-to-end: build the prescribed Sybil population, run it through the
    real aggregation, and confirm the target overtakes the defender."""
    honest = (
        _forecast("f-1", "honest-1", 0.20, 30, "cluster-a"),
        _forecast("f-2", "honest-2", 0.25, 20, "cluster-b"),
        _forecast("f-3", "honest-3", 0.15, 25, "cluster-c"),
    )
    agents = {
        "honest-1": _agent("honest-1", 0.70),
        "honest-2": _agent("honest-2", 0.65),
        "honest-3": _agent("honest-3", 0.50),
    }
    honest_result = aggregate_probability(honest, agents, 0.30)
    honest_weight = sum(c.weight for c in honest_result.contributions)

    cost = stakeroute_attack_cost(
        defender_priority=DEFENDER_PRIORITY,
        impact_minor_units=TARGET_IMPACT,
        urgency=TARGET_URGENCY,
        review_cost=TARGET_REVIEW_COST,
        honest_weight=honest_weight,
        honest_probability=honest_result.probability,
        reputation_per_identity=0.1,
        stake_per_identity=50,
    )
    assert cost.feasible
    assert cost.identities > 0

    sybils = tuple(
        _forecast(f"s-{i}", f"sybil-{i}", PROBABILITY_CEIL, 50, f"sybil-cluster-{i}")
        for i in range(cost.identities)
    )
    for i in range(cost.identities):
        agents[f"sybil-{i}"] = _agent(f"sybil-{i}", 0.1)

    attacked = aggregate_probability(honest + sybils, agents, 0.30)
    attacked_priority = priority_score(
        attacked.probability, TARGET_IMPACT, TARGET_URGENCY, TARGET_REVIEW_COST
    )
    assert attacked_priority >= DEFENDER_PRIORITY

    # The attack is priced with its own consequence attached.
    assert cost.credits == cost.identities * 50
    assert cost.settlement_loss_credits > 0

    # One identity fewer must not be enough — the count is a threshold,
    # not a loose over-estimate.
    just_under = aggregate_probability(honest + sybils[:-1], agents, 0.30)
    just_under_priority = priority_score(
        just_under.probability, TARGET_IMPACT, TARGET_URGENCY, TARGET_REVIEW_COST
    )
    assert just_under_priority < DEFENDER_PRIORITY


def test_stakeroute_attack_is_infeasible_when_impact_is_too_low() -> None:
    """A hypothesis cheap enough to review cannot be promoted at any price."""
    cost = stakeroute_attack_cost(
        defender_priority=DEFENDER_PRIORITY,
        impact_minor_units=5_000_000,
        urgency=0.3,
        review_cost=1.0,
        honest_weight=4.0,
        honest_probability=0.2,
        reputation_per_identity=1.0,
        stake_per_identity=50,
    )
    assert not cost.feasible
    assert cost.required_probability > PROBABILITY_CEIL
    assert "unreachable" in cost.note


def test_attack_on_an_unopposed_hypothesis_costs_one_identity() -> None:
    cost = stakeroute_attack_cost(
        defender_priority=DEFENDER_PRIORITY,
        impact_minor_units=2_000_000_000,
        urgency=1.0,
        review_cost=1.0,
        honest_weight=0.0,
        honest_probability=0.0,
        reputation_per_identity=0.1,
        stake_per_identity=1,
    )
    assert cost.feasible
    assert cost.identities == 1


def test_majority_vote_count_flips_the_real_baseline() -> None:
    honest = (
        _forecast("f-1", "a", 0.20, 30, "c-a"),
        _forecast("f-2", "b", 0.25, 20, "c-b"),
        _forecast("f-3", "c", 0.60, 25, "c-c"),
    )
    votes_true = sum(1 for f in honest if f.probability > 0.5)
    threshold = 0.75

    needed = majority_vote_identities_required(len(honest), votes_true, threshold)
    assert needed is not None

    flooded = honest + tuple(
        _forecast(f"s-{i}", f"s-{i}", 0.9, 1, f"c-s-{i}") for i in range(needed)
    )
    assert majority_vote_probability(flooded, 0.3) > threshold

    one_fewer = honest + tuple(
        _forecast(f"s-{i}", f"s-{i}", 0.9, 1, f"c-s-{i}") for i in range(needed - 1)
    )
    assert majority_vote_probability(one_fewer, 0.3) <= threshold


def test_majority_vote_needs_nobody_when_already_above() -> None:
    assert majority_vote_identities_required(4, 4, 0.75) == 0
    # An exact tie is not a win: the tie-break favours the defender.
    assert majority_vote_identities_required(4, 3, 0.75) == 1


def test_majority_vote_cannot_reach_certainty() -> None:
    assert majority_vote_identities_required(4, 1, 1.0) is None


def test_highest_confidence_falls_to_a_single_identity() -> None:
    assert highest_confidence_identities_required(0.4, 0.9) == 1
    assert highest_confidence_identities_required(0.95, 0.9) == 0
    assert highest_confidence_identities_required(0.4, 0.995) is None
    # An exact tie still needs one identity to break it.
    assert highest_confidence_identities_required(0.9, 0.9) == 1


def test_baseline_attack_cost_commits_no_capital() -> None:
    """The comparison that matters: no stake, no reputation, no loss."""
    cost = baseline_attack_cost(
        strategy="majority_vote",
        defender_probability=DEFENDER_PROBABILITY,
        honest_forecast_count=6,
        honest_votes_true=2,
        current_max_probability=0.6,
    )
    assert cost.feasible
    assert cost.identities > 0
    assert cost.credits == 0
    assert cost.reputation_per_identity == 0.0
    assert cost.settlement_loss_credits == 0


def test_baseline_attack_cost_rejects_unknown_strategy() -> None:
    with pytest.raises(InfeasibleAttack):
        baseline_attack_cost(
            strategy="stakeroute",
            defender_probability=DEFENDER_PROBABILITY,
            honest_forecast_count=6,
            honest_votes_true=2,
            current_max_probability=0.6,
        )


def test_frontier_is_monotonically_cheaper_as_reputation_rises() -> None:
    frontier = attack_frontier(
        defender_priority=DEFENDER_PRIORITY,
        impact_minor_units=TARGET_IMPACT,
        urgency=TARGET_URGENCY,
        review_cost=TARGET_REVIEW_COST,
        honest_weight=4.0,
        honest_probability=0.2,
        reputations=(0.1, 0.3, 0.5, 0.7, 1.0),
        stake_per_identity=50,
    )
    assert len(frontier) == 5
    assert all(isinstance(point, AttackCost) for point in frontier)

    counts = [point.identities for point in frontier]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] > counts[-1]
