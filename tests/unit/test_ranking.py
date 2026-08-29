"""Tests for priority scoring and budgeted attention allocation."""

import random

from stakeroute.core.ranking import allocate_attention, priority_score
from stakeroute.core.types import RankedHypothesis


def _ranked(
    hid: str, probability: float, priority: float, impact: int
) -> RankedHypothesis:
    return RankedHypothesis(
        hypothesis_id=hid,
        probability=probability,
        priority=priority,
        impact_minor_units=impact,
    )


def test_never_returns_more_than_budget() -> None:
    ranked = tuple(
        _ranked(f"h{i}", probability=0.5, priority=float(i), impact=100)
        for i in range(10)
    )
    result = allocate_attention(ranked, budget=2)
    routed = [d for d in result.decisions if d.routed]
    assert len(routed) == 2
    assert result.withheld_count == 8


def test_high_impact_moderate_probability_outranks_high_probability_low_impact() -> (
    None
):
    high_impact = priority_score(
        probability=0.6, impact_minor_units=800_000_000, urgency=1.0, review_cost=1.0
    )
    high_probability = priority_score(
        probability=0.95, impact_minor_units=1000, urgency=1.0, review_cost=1.0
    )
    assert high_impact > high_probability


def test_withheld_count_matches_candidates_beyond_budget() -> None:
    ranked = tuple(
        _ranked(f"h{i}", probability=0.5, priority=10.0 - i, impact=100)
        for i in range(5)
    )
    result = allocate_attention(ranked, budget=2)
    assert result.withheld_count == 3
    assert sum(1 for d in result.decisions if not d.routed) == 3


def test_ties_deterministic_across_shuffles() -> None:
    base = [
        _ranked("h-a", probability=0.5, priority=10.0, impact=500),
        _ranked("h-b", probability=0.5, priority=10.0, impact=500),
        _ranked("h-c", probability=0.5, priority=10.0, impact=500),
    ]
    rng = random.Random(1234)
    first_result = None
    for _ in range(100):
        shuffled = base[:]
        rng.shuffle(shuffled)
        result = allocate_attention(tuple(shuffled), budget=2)
        order = tuple(d.hypothesis_id for d in result.decisions)
        if first_result is None:
            first_result = order
        assert order == first_result


def test_review_cost_must_be_positive() -> None:
    import pytest

    with pytest.raises(ValueError):
        priority_score(
            probability=0.5, impact_minor_units=100, urgency=1.0, review_cost=0
        )
