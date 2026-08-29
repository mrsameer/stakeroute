"""Tests for the bounded reputation update."""

import random

from stakeroute.core.reputation import (
    REPUTATION_CEIL,
    REPUTATION_FLOOR,
    update_reputation,
)


def test_bounds_hold_under_adversarial_input() -> None:
    rng = random.Random(7)
    for _ in range(2000):
        current = rng.uniform(0.0, 1.5)
        brier = rng.uniform(-1.0, 2.0)
        improvement = rng.uniform(-2.0, 2.0)
        decay = rng.uniform(0.0, 1.0)
        result = update_reputation(current, brier, improvement, decay)
        assert REPUTATION_FLOOR <= result <= REPUTATION_CEIL


def test_recency_dominates_stale_history() -> None:
    # An agent with a strong start but a poor recent result should end
    # below where it started, because decay weights the recent settlement.
    reputation = 0.9
    for _ in range(5):
        # Poor calibration: high brier, no improvement over the prior.
        reputation = update_reputation(
            reputation, brier_score=0.8, improvement=-0.2, decay=0.4
        )
    assert reputation < 0.9


def test_floor_retains_a_recovery_path() -> None:
    reputation = REPUTATION_FLOOR
    improved = update_reputation(
        reputation, brier_score=0.01, improvement=0.9, decay=0.5
    )
    assert improved > REPUTATION_FLOOR


def test_result_never_exceeds_ceiling_after_strong_run() -> None:
    reputation = 0.95
    for _ in range(10):
        reputation = update_reputation(
            reputation, brier_score=0.0, improvement=1.0, decay=0.9
        )
    assert reputation <= REPUTATION_CEIL
