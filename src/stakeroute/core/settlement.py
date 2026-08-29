"""Integer credit settlement against a resolved outcome (FR-025, FR-026).

Constitution Principle III requires exact replay-equality, not within-epsilon
agreement. Two Decimal roundings — one on the score improvement, one on the
final stake-weighted delta — keep floating-point noise from the Brier-score
subtraction out of the stored integer ledger, and both use round-half-to-even
so the result never depends on which direction ties happen to break.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from stakeroute.core.scoring import brier_score
from stakeroute.core.types import Settlement


def settle_forecast(
    forecast_id: str,
    stake: int,
    prior_probability: float,
    probability: float,
    outcome: int,
    scale: int = 100,
) -> Settlement:
    """Score a forecast against a resolved outcome and compute its integer
    credit delta.

    The delta is proportional to both the stake and the improvement over the
    hypothesis prior, and is never allowed below ``-stake`` — a forecast can
    cost an agent at most what it staked (SC-009).
    """
    prior_brier = brier_score(prior_probability, outcome)
    forecast_brier = brier_score(probability, outcome)
    improvement = prior_brier - forecast_brier

    # Round the improvement to 1/scale precision first, so float noise from
    # the Brier subtraction cannot leak into the integer credit delta.
    quantum = Decimal(1) / Decimal(scale)
    improvement_dec = Decimal(str(improvement)).quantize(
        quantum, rounding=ROUND_HALF_EVEN
    )
    delta_dec = (Decimal(stake) * improvement_dec).quantize(
        Decimal(1), rounding=ROUND_HALF_EVEN
    )
    credit_delta = max(int(delta_dec), -stake)

    return Settlement(
        forecast_id=forecast_id,
        brier_score=forecast_brier,
        prior_brier_score=prior_brier,
        improvement=improvement,
        credit_delta=credit_delta,
    )
