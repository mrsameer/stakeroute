"""Proper probabilistic scoring rule (FR-025)."""

from __future__ import annotations

from stakeroute.core.types import InvalidOutcome


def brier_score(probability: float, outcome: int) -> float:
    """Return ``(probability - outcome) ** 2``.

    Rewards calibration rather than binary correctness — a forecast of 0.9
    against a true outcome scores far better than 0.6 against the same
    outcome, even though both would be "correct" under a threshold rule.

    Raises:
        InvalidOutcome: if ``outcome`` is not ``0`` or ``1``.
    """
    if outcome not in (0, 1):
        raise InvalidOutcome(outcome)
    return (probability - outcome) ** 2
