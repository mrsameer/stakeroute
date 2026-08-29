"""Tests for integer credit settlement."""

import random

import pytest

from stakeroute.core.settlement import settle_forecast


def test_spec_worked_example_improvement() -> None:
    result = settle_forecast(
        forecast_id="f-1", stake=30, prior_probability=0.30, probability=0.90, outcome=1
    )
    assert result.improvement == pytest.approx(0.48)


def test_credit_delta_is_integer() -> None:
    result = settle_forecast(
        forecast_id="f-1", stake=30, prior_probability=0.30, probability=0.90, outcome=1
    )
    assert isinstance(result.credit_delta, int)


def test_loss_never_exceeds_stake_across_parameter_grid() -> None:
    rng = random.Random(42)
    for _ in range(2000):
        stake = rng.randint(1, 50)
        prior = rng.uniform(0.01, 0.99)
        probability = rng.uniform(0.01, 0.99)
        outcome = rng.choice([0, 1])
        result = settle_forecast(
            forecast_id="f-x",
            stake=stake,
            prior_probability=prior,
            probability=probability,
            outcome=outcome,
        )
        assert result.credit_delta >= -stake, (
            f"stake={stake} prior={prior} p={probability} outcome={outcome} "
            f"delta={result.credit_delta}"
        )


def test_confidently_wrong_agent_loses_capped_at_stake() -> None:
    result = settle_forecast(
        forecast_id="f-1", stake=20, prior_probability=0.30, probability=0.99, outcome=0
    )
    assert result.credit_delta < 0
    assert result.credit_delta >= -20
