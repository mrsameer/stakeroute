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
    indefinitely (FR-027 acceptance scenario 3). Reputation moves in the
    same direction as the credit settlement — beating the prior always
    pulls reputation up, falling short of it always pulls reputation down
    (SC-008) — never the reverse, even for a forecast whose absolute Brier
    score is still mediocre. The floor always leaves a recovery path —
    reputation is clamped, never permanently zeroed.

    ``brier_score`` is accepted for forward compatibility (a future
    version may use it to scale the *magnitude* of the update) but does
    not currently influence the result — see D-006's note on keeping the
    mechanism defensible: a sign that depended on two competing terms
    would be one more thing to explain under questioning.
    """
    del brier_score  # not yet used; kept in the signature, see docstring
    signal = improvement
    target = current + signal
    blended = (1 - decay) * current + decay * target
    if blended < REPUTATION_FLOOR:
        return REPUTATION_FLOOR
    if blended > REPUTATION_CEIL:
        return REPUTATION_CEIL
    return blended
