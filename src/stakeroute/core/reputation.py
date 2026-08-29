"""Bounded, recency-weighted reputation update (FR-027)."""

from __future__ import annotations

REPUTATION_FLOOR = 0.1
REPUTATION_CEIL = 1.0


def update_reputation(
    current: float,
    brier_score: float,
    improvement: float,
    decay: float = 0.3,
) -> float:
    """Return the new reputation after one settlement, clamped to
    ``[0.1, 1.0]``.

    ``decay`` is the weight given to this settlement's result relative to
    the agent's prior standing — a higher value makes recent performance
    dominate faster, so historical standing decays rather than persisting
    indefinitely (FR-027 acceptance scenario 3). A well-calibrated forecast
    (positive ``improvement``, low ``brier_score``) pulls reputation up;
    a poorly-calibrated one pulls it down. The floor always leaves a
    recovery path — reputation is clamped, never permanently zeroed.
    """
    # Calibration signal in [-1, 1]: reward beating the prior, penalize a
    # high absolute Brier score even when improvement is small.
    signal = improvement - brier_score
    target = current + signal
    blended = (1 - decay) * current + decay * target
    if blended < REPUTATION_FLOOR:
        return REPUTATION_FLOOR
    if blended > REPUTATION_CEIL:
        return REPUTATION_CEIL
    return blended
