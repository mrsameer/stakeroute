"""SC-006: identical seed produces identical signals, rankings and balances.

Exercises the simulator and the deterministic core together, with no
transport or storage in the loop yet (Phase 2 checkpoint) — the mechanism
must be proven reproducible before anything is built on top of it.
"""

from stakeroute.core.market import aggregate_probability
from stakeroute.core.ranking import allocate_attention, priority_score
from stakeroute.core.settlement import settle_forecast
from stakeroute.core.types import AgentSnapshot, ForecastSnapshot, RankedHypothesis
from stakeroute.simulator.scenarios import generate_world


def _run_ranking_pass(world):
    agents = {
        profile.agent_id: AgentSnapshot(
            id=profile.agent_id,
            reputation=profile.starting_reputation,
            available_credits=100,
            staked_credits=0,
            attested=profile.attested,
            created_at_ms=0,
        )
        for profile in world.agents
    }

    ranked = []
    aggregates = {}
    for hypothesis in world.hypotheses:
        forecasts = tuple(
            ForecastSnapshot(
                id=f.source_event_id,
                agent_id=f.agent_id,
                hypothesis_id=f.hypothesis_id,
                probability=f.probability,
                stake=f.stake,
                evidence_cluster_id=f.evidence_cluster_id,
            )
            for f in world.forecasts
            if f.hypothesis_id == hypothesis.id
        )
        result = aggregate_probability(forecasts, agents, hypothesis.prior_probability)
        aggregates[hypothesis.id] = result
        priority = priority_score(
            result.probability,
            hypothesis.impact_minor_units,
            hypothesis.urgency,
            hypothesis.review_cost,
        )
        ranked.append(
            RankedHypothesis(
                hypothesis_id=hypothesis.id,
                probability=result.probability,
                priority=priority,
                impact_minor_units=hypothesis.impact_minor_units,
            )
        )

    allocation = allocate_attention(tuple(ranked), budget=2)

    balances = {}
    for hypothesis in world.hypotheses:
        for forecast in world.forecasts:
            if forecast.hypothesis_id != hypothesis.id:
                continue
            settlement = settle_forecast(
                forecast_id=forecast.source_event_id,
                stake=forecast.stake,
                prior_probability=hypothesis.prior_probability,
                probability=forecast.probability,
                outcome=hypothesis.ground_truth,
            )
            balances[forecast.source_event_id] = settlement.credit_delta

    return aggregates, allocation, balances


def test_same_seed_produces_identical_signals() -> None:
    world_a = generate_world(seed=42)
    world_b = generate_world(seed=42)
    assert world_a.signals == world_b.signals
    assert world_a.forecasts == world_b.forecasts


def test_same_seed_produces_identical_rankings_and_balances() -> None:
    world_a = generate_world(seed=42)
    world_b = generate_world(seed=42)

    aggregates_a, allocation_a, balances_a = _run_ranking_pass(world_a)
    aggregates_b, allocation_b, balances_b = _run_ranking_pass(world_b)

    for hid in aggregates_a:
        assert aggregates_a[hid].probability == aggregates_b[hid].probability

    assert allocation_a.decisions == allocation_b.decisions
    assert allocation_a.withheld_count == allocation_b.withheld_count
    assert balances_a == balances_b


def test_different_seeds_can_diverge() -> None:
    world_a = generate_world(seed=1)
    world_b = generate_world(seed=2)
    assert world_a.signals != world_b.signals
