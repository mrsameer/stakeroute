"""Tests for the Brier scoring rule."""

import pytest

from stakeroute.core.scoring import brier_score
from stakeroute.core.types import InvalidOutcome


def test_known_values() -> None:
    assert brier_score(0.9, 1) == pytest.approx(0.01)
    assert brier_score(0.9, 0) == pytest.approx(0.81)
    assert brier_score(0.5, 1) == pytest.approx(0.25)
    assert brier_score(1.0, 1) == pytest.approx(0.0)


def test_raises_for_non_binary_outcome() -> None:
    with pytest.raises(InvalidOutcome):
        brier_score(0.5, 2)
    with pytest.raises(InvalidOutcome):
        brier_score(0.5, -1)
