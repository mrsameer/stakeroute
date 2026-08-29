"""Tests for the model response validators (contracts/model-boundary.md, FR-122).

Every rejection reason in the contract must be reachable and distinguishable
— a validator that accepts everything passes a badly written test suite
silently (tasks.md notes).
"""

from __future__ import annotations

from stakeroute.model.protocol import ForecastDraft, ProposalDraft
from stakeroute.model.validation import validate_forecast, validate_proposal

WINDOW_START_MS = 1_000_000
WINDOW_END_MS = 2_000_000
KNOWN_IDS = {"e1", "e2", "e3"}
OBS_TS = {"e1": 1_500_000, "e2": 1_600_000, "e3": 3_000_000}  # e3 is out of window


def _valid_proposal_raw() -> dict:
    return {
        "statement": "The disk is filling up on /data",
        "cited_observation_ids": ["e1", "e2"],
        "condition_name": "disk_free_below",
        "condition_params": {"mount": "/data", "pct": 10},
    }


def _validate(raw: dict):
    return validate_proposal(
        raw,
        known_observation_ids=KNOWN_IDS,
        observation_ts_by_id=OBS_TS,
        window_start_ms=WINDOW_START_MS,
        window_end_ms=WINDOW_END_MS,
    )


# -- proposal validation ----------------------------------------------------


def test_valid_proposal_is_accepted() -> None:
    result = _validate(_valid_proposal_raw())
    assert isinstance(result, ProposalDraft)
    assert result.statement == "The disk is filling up on /data"
    assert result.cited_observation_ids == ("e1", "e2")
    assert result.condition_name == "disk_free_below"


def test_proposal_with_no_condition_is_accepted() -> None:
    raw = _valid_proposal_raw()
    raw["condition_name"] = None
    raw["condition_params"] = None
    result = _validate(raw)
    assert isinstance(result, ProposalDraft)
    assert result.condition_name is None


def test_malformed_shape_missing_statement() -> None:
    raw = _valid_proposal_raw()
    del raw["statement"]
    assert _validate(raw) == "MALFORMED_SHAPE"


def test_malformed_shape_empty_statement() -> None:
    raw = _valid_proposal_raw()
    raw["statement"] = ""
    assert _validate(raw) == "MALFORMED_SHAPE"


def test_malformed_shape_statement_too_long() -> None:
    raw = _valid_proposal_raw()
    raw["statement"] = "x" * 501
    assert _validate(raw) == "MALFORMED_SHAPE"


def test_no_citations() -> None:
    raw = _valid_proposal_raw()
    raw["cited_observation_ids"] = []
    assert _validate(raw) == "NO_CITATIONS"


def test_unknown_citation() -> None:
    raw = _valid_proposal_raw()
    raw["cited_observation_ids"] = ["e1", "does-not-exist"]
    assert _validate(raw) == "UNKNOWN_CITATION"


def test_citation_out_of_window() -> None:
    raw = _valid_proposal_raw()
    raw["cited_observation_ids"] = ["e1", "e3"]  # e3 is outside the window
    assert _validate(raw) == "CITATION_OUT_OF_WINDOW"


def test_unknown_condition() -> None:
    raw = _valid_proposal_raw()
    raw["condition_name"] = "not_a_real_condition"
    assert _validate(raw) == "UNKNOWN_CONDITION"


def test_invalid_condition_params_missing_key() -> None:
    raw = _valid_proposal_raw()
    raw["condition_params"] = {"mount": "/data"}  # missing "pct"
    assert _validate(raw) == "INVALID_CONDITION_PARAMS"


def test_invalid_condition_params_extra_key() -> None:
    raw = _valid_proposal_raw()
    raw["condition_params"] = {"mount": "/data", "pct": 10, "extra": 1}
    assert _validate(raw) == "INVALID_CONDITION_PARAMS"


def test_proposal_refusal() -> None:
    raw = {"refusal": "I cannot determine a hypothesis from this evidence."}
    assert _validate(raw) == "REFUSAL"


# -- forecast validation ------------------------------------------------


IN_SCOPE = frozenset({"host.metrics"})
ALL_SOURCES = frozenset(
    {"host.metrics", "app.logs", "repo.vcs_tests", "container.events"}
)


def _valid_forecast_raw() -> dict:
    return {
        "probability": 0.72,
        "stake": 20,
        "rationale": "CPU has been pinned at 98% for three consecutive polls.",
    }


def _validate_forecast(raw: dict, available_credits: int = 100):
    return validate_forecast(
        raw,
        in_scope_sources=IN_SCOPE,
        all_source_ids=ALL_SOURCES,
        available_credits=available_credits,
    )


def test_valid_forecast_is_accepted() -> None:
    result = _validate_forecast(_valid_forecast_raw())
    assert isinstance(result, ForecastDraft)
    assert result.probability == 0.72
    assert result.stake == 20


def test_forecast_malformed_shape_missing_field() -> None:
    raw = _valid_forecast_raw()
    del raw["stake"]
    assert _validate_forecast(raw) == "MALFORMED_SHAPE"


def test_forecast_malformed_shape_empty_rationale() -> None:
    raw = _valid_forecast_raw()
    raw["rationale"] = ""
    assert _validate_forecast(raw) == "MALFORMED_SHAPE"


def test_probability_out_of_range_high() -> None:
    raw = _valid_forecast_raw()
    raw["probability"] = 1.5
    assert _validate_forecast(raw) == "PROBABILITY_OUT_OF_RANGE"


def test_probability_out_of_range_low() -> None:
    raw = _valid_forecast_raw()
    raw["probability"] = -0.1
    assert _validate_forecast(raw) == "PROBABILITY_OUT_OF_RANGE"


def test_stake_out_of_range() -> None:
    raw = _valid_forecast_raw()
    raw["stake"] = 10_000
    assert _validate_forecast(raw) == "STAKE_OUT_OF_RANGE"


def test_insufficient_credits() -> None:
    raw = _valid_forecast_raw()
    raw["stake"] = 20
    assert _validate_forecast(raw, available_credits=5) == "INSUFFICIENT_CREDITS"


def test_evidence_scope_violation() -> None:
    raw = _valid_forecast_raw()
    raw["rationale"] = "Combined with app.logs showing repeated errors, this is bad."
    assert _validate_forecast(raw) == "EVIDENCE_SCOPE_VIOLATION"


def test_forecast_refusal() -> None:
    raw = {"refusal": "insufficient evidence to forecast"}
    assert _validate_forecast(raw) == "REFUSAL"
