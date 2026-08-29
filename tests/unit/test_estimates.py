"""Tests for the pure attribute estimators (FR-108, D-016).

Every estimator here is a deterministic function from cited
``ObservationSnapshot``s to an ``AttributeEstimate`` — no I/O, no clock
read except the explicit ``now_ms`` argument, no model output anywhere
near it.
"""

from __future__ import annotations

from stakeroute.core.estimates import (
    estimate_impact,
    estimate_review_cost,
    estimate_urgency,
)
from stakeroute.core.types import ObservationSnapshot


def _obs(
    event_id: str,
    source: str = "host.metrics",
    observed_at_ms: int = 1_000_000,
    severity: float = 0.5,
    payload: dict | None = None,
) -> ObservationSnapshot:
    return ObservationSnapshot(
        event_id=event_id,
        source=source,
        observed_at_ms=observed_at_ms,
        payload=payload or {},
        severity=severity,
    )


# -- impact -------------------------------------------------------------


def test_impact_grows_with_observation_count() -> None:
    few = (_obs("a"),)
    many = (_obs("a"), _obs("b"), _obs("c"), _obs("d"))
    fewer_estimate = estimate_impact(few)
    more_estimate = estimate_impact(many)
    assert more_estimate.value > fewer_estimate.value


def test_impact_grows_with_severity() -> None:
    mild = (_obs("a", severity=0.1),)
    severe = (_obs("a", severity=0.9),)
    assert estimate_impact(severe).value > estimate_impact(mild).value


def test_impact_carries_basis_and_estimator() -> None:
    estimate = estimate_impact((_obs("a"),))
    assert estimate.attribute == "impact"
    assert estimate.basis
    assert estimate.estimator == "estimate_impact"


def test_impact_raises_on_empty_observations() -> None:
    import pytest

    with pytest.raises(ValueError):
        estimate_impact(())


# -- urgency --------------------------------------------------------------


def test_urgency_is_higher_for_more_recent_observations() -> None:
    now_ms = 2_000_000
    recent = (_obs("a", observed_at_ms=1_990_000),)
    stale = (_obs("a", observed_at_ms=100_000),)
    recent_estimate = estimate_urgency(recent, now_ms=now_ms)
    stale_estimate = estimate_urgency(stale, now_ms=now_ms)
    assert recent_estimate.value > stale_estimate.value


def test_urgency_stays_in_unit_interval() -> None:
    now_ms = 2_000_000
    for observed_at_ms in (0, 1_000_000, 1_999_999, 2_000_000, 2_500_000):
        estimate = estimate_urgency((_obs("a", observed_at_ms=observed_at_ms),), now_ms)
        assert 0.0 <= estimate.value <= 1.0


def test_urgency_carries_basis_and_estimator() -> None:
    estimate = estimate_urgency((_obs("a"),), now_ms=1_000_000)
    assert estimate.attribute == "urgency"
    assert estimate.basis
    assert estimate.estimator == "estimate_urgency"


def test_urgency_raises_on_empty_observations() -> None:
    import pytest

    with pytest.raises(ValueError):
        estimate_urgency((), now_ms=1_000_000)


# -- review cost ------------------------------------------------------------


def test_review_cost_grows_with_scope() -> None:
    single_source = (_obs("a", source="host.metrics"), _obs("b", source="host.metrics"))
    multi_source = (
        _obs("a", source="host.metrics"),
        _obs("b", source="app.logs"),
        _obs("c", source="repo.vcs_tests"),
    )
    assert (
        estimate_review_cost(multi_source).value
        > estimate_review_cost(single_source).value
    )


def test_review_cost_carries_basis_and_estimator() -> None:
    estimate = estimate_review_cost((_obs("a"),))
    assert estimate.attribute == "review_cost"
    assert estimate.basis
    assert estimate.estimator == "estimate_review_cost"


def test_review_cost_is_strictly_positive() -> None:
    estimate = estimate_review_cost((_obs("a"),))
    assert estimate.value > 0


def test_review_cost_raises_on_empty_observations() -> None:
    import pytest

    with pytest.raises(ValueError):
        estimate_review_cost(())
