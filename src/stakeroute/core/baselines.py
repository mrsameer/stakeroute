"""Deliberately naive baseline strategies (FR-023).

Both are written to fail on purpose. A mechanism that stops discriminating
between StakeRoute and these baselines under attack breaks a test rather
than passing silently — that is the point of keeping them this simple.
"""

from __future__ import annotations

from stakeroute.core.types import ForecastSnapshot


def majority_vote_probability(
    forecasts: tuple[ForecastSnapshot, ...], prior_probability: float
) -> float:
    """One unweighted vote per forecast above 0.5.

    Every agent identity counts equally regardless of reputation, stake, or
    evidence independence — which is exactly what a Sybil flood exploits.
    Returns the hypothesis prior when there are no forecasts.
    """
    if not forecasts:
        return prior_probability
    votes_true = sum(1 for f in forecasts if f.probability > 0.5)
    return votes_true / len(forecasts)


def highest_confidence_probability(
    forecasts: tuple[ForecastSnapshot, ...], prior_probability: float
) -> float:
    """Rank by the single highest self-reported probability.

    A lone confidently-wrong forecast overrides an entire population of
    correct, well-calibrated ones. Returns the hypothesis prior when there
    are no forecasts.
    """
    if not forecasts:
        return prior_probability
    return max(f.probability for f in forecasts)
