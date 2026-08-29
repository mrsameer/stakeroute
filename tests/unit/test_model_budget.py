"""Tests for the per-interval model usage ceiling (FR-125, D-020).

Exhaustion degrades capability — never the decision path, which never
holds a reference to this module at all (that architectural claim is
tested separately at T077/T074; this file is about the budget mechanism
itself).
"""

from __future__ import annotations

from stakeroute.model.budget import ModelBudget, capability_for_purpose
from stakeroute.model.protocol import (
    CAPABILITY_HYPOTHESIS_PROPOSAL,
    CAPABILITY_PROSE_EXPLANATION,
)


def test_has_capacity_below_ceiling() -> None:
    budget = ModelBudget(ceiling=3)
    now = 1_000_000
    assert budget.has_capacity(now)
    budget.record_call(now)
    budget.record_call(now)
    assert budget.has_capacity(now)


def test_ceiling_reached_after_exhaustion() -> None:
    budget = ModelBudget(ceiling=3)
    now = 1_000_000
    for _ in range(3):
        budget.record_call(now)
    assert not budget.has_capacity(now)


def test_consumption_is_reported_against_the_ceiling() -> None:
    budget = ModelBudget(ceiling=5)
    now = 1_000_000
    for _ in range(2):
        budget.record_call(now)
    assert budget.calls_this_interval(now) == 2
    assert budget.ceiling() == 5


def test_calls_outside_the_window_do_not_count() -> None:
    budget = ModelBudget(ceiling=2)
    start = 1_000_000
    budget.record_call(start)
    budget.record_call(start)
    assert not budget.has_capacity(start)

    # An hour and one millisecond later, both earlier calls have aged out.
    later = start + ModelBudget.WINDOW_MS + 1
    assert budget.has_capacity(later)
    assert budget.calls_this_interval(later) == 0


def test_capability_for_purpose_names_the_specific_capability() -> None:
    assert capability_for_purpose("proposal") == CAPABILITY_HYPOTHESIS_PROPOSAL
    assert capability_for_purpose("forecast") == CAPABILITY_HYPOTHESIS_PROPOSAL
    assert capability_for_purpose("explanation") == CAPABILITY_PROSE_EXPLANATION


def test_capability_for_purpose_rejects_unknown_purpose() -> None:
    import pytest

    with pytest.raises(ValueError):
        capability_for_purpose("settlement")


def test_unavailable_capabilities_never_names_ranking_or_settlement() -> None:
    # The contract is explicit: the only two values this list ever contains
    # are hypothesis_proposal and prose_explanation.
    for purpose in ("proposal", "forecast", "explanation"):
        capability = capability_for_purpose(purpose)
        assert capability in (
            CAPABILITY_HYPOTHESIS_PROPOSAL,
            CAPABILITY_PROSE_EXPLANATION,
        )
