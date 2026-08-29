"""Reputation decay over real elapsed time (FR-137, D-021).

Additive only: ``core/reputation.py::update_reputation`` is not modified by
this module, so every feature-001 result still reproduces byte-identically
(SC-108). This is applied by the worker at epoch rollover in real mode
only, with ``elapsed_ms`` passed in as an argument — the core still reads
no clock (Principle I).
"""

from __future__ import annotations

from stakeroute.core.reputation import REPUTATION_FLOOR


def decay_reputation(current: float, elapsed_ms: int, half_life_ms: int) -> float:
    """Exponentially decay ``current`` toward ``REPUTATION_FLOOR``.

    Standing that is not reinforced by new settlements erodes over real
    time rather than persisting indefinitely: every ``half_life_ms`` of
    elapsed time without reinforcement halves the distance remaining to
    the floor. The floor is approached asymptotically and never becomes
    absorbing — a reputation exactly at the floor decays no further, and a
    reputation above it never crosses below.

    Raises:
        ValueError: if ``elapsed_ms`` is negative or ``half_life_ms`` is
            not strictly positive.
    """
    if elapsed_ms < 0:
        raise ValueError(f"elapsed_ms must be >= 0, got {elapsed_ms!r}")
    if half_life_ms <= 0:
        raise ValueError(f"half_life_ms must be > 0, got {half_life_ms!r}")

    factor = 0.5 ** (elapsed_ms / half_life_ms)
    return REPUTATION_FLOOR + (current - REPUTATION_FLOOR) * factor
