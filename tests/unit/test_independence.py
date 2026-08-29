"""Tests for the evidence-independence discount (FR-015)."""

import itertools
import math

import pytest

from stakeroute.core.independence import independence_factor
from stakeroute.core.types import EmptyCluster


def test_full_weight_at_cluster_size_one() -> None:
    assert independence_factor(1) == 1.0


def test_monotonic_decrease_with_cluster_size() -> None:
    values = [independence_factor(n) for n in range(1, 51)]
    for earlier, later in itertools.pairwise(values):
        assert later < earlier


def test_matches_inverse_sqrt() -> None:
    for n in (1, 4, 9, 16, 25, 100):
        assert independence_factor(n) == pytest.approx(1.0 / math.sqrt(n))


def test_raises_below_one() -> None:
    with pytest.raises(EmptyCluster):
        independence_factor(0)
    with pytest.raises(EmptyCluster):
        independence_factor(-3)
