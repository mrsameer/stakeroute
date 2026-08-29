"""Tests for reputation decay over real elapsed time (FR-137, D-021).

``decay_reputation`` is additive to the mechanism, never a replacement for
``core/reputation.py::update_reputation`` — see D-021's note that changing
the settlement-time update would break SC-108 on the first commit.
"""

from __future__ import annotations

import pytest

from stakeroute.core.decay import decay_reputation
from stakeroute.core.reputation import REPUTATION_FLOOR

HALF_LIFE_MS = 7 * 24 * 60 * 60 * 1000  # one week


def test_zero_elapsed_is_identity() -> None:
    assert decay_reputation(0.8, elapsed_ms=0, half_life_ms=HALF_LIFE_MS) == 0.8


def test_one_half_life_halves_the_distance_to_floor() -> None:
    current = 0.9
    decayed = decay_reputation(
        current, elapsed_ms=HALF_LIFE_MS, half_life_ms=HALF_LIFE_MS
    )
    expected = REPUTATION_FLOOR + (current - REPUTATION_FLOOR) * 0.5
    assert decayed == pytest.approx(expected)


def test_two_half_lives_quarters_the_distance_to_floor() -> None:
    current = 0.9
    decayed = decay_reputation(
        current, elapsed_ms=2 * HALF_LIFE_MS, half_life_ms=HALF_LIFE_MS
    )
    expected = REPUTATION_FLOOR + (current - REPUTATION_FLOOR) * 0.25
    assert decayed == pytest.approx(expected)


def test_decay_monotonically_approaches_the_floor_but_never_reaches_it() -> None:
    current = 0.9
    previous = current
    for weeks in range(1, 20):
        decayed = decay_reputation(
            current, elapsed_ms=weeks * HALF_LIFE_MS, half_life_ms=HALF_LIFE_MS
        )
        assert decayed < previous
        assert decayed > REPUTATION_FLOOR
        previous = decayed


def test_reputation_already_at_floor_stays_at_floor() -> None:
    decayed = decay_reputation(
        REPUTATION_FLOOR, elapsed_ms=HALF_LIFE_MS * 5, half_life_ms=HALF_LIFE_MS
    )
    assert decayed == pytest.approx(REPUTATION_FLOOR)


def test_negative_elapsed_ms_raises() -> None:
    with pytest.raises(ValueError):
        decay_reputation(0.5, elapsed_ms=-1, half_life_ms=HALF_LIFE_MS)


def test_non_positive_half_life_raises() -> None:
    with pytest.raises(ValueError):
        decay_reputation(0.5, elapsed_ms=1000, half_life_ms=0)
