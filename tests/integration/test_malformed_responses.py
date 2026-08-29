"""FR-122, SC-106: every rejection reason produces a ``model_interactions``
row with ``accepted = 0`` and zero downstream forecasts, hypotheses or
economic effects — swept systematically across both the proposal and
forecast response shapes.
"""

from __future__ import annotations

import pytest

from stakeroute.core.types import ObservationSnapshot
from stakeroute.model.protocol import Accepted, ModelStateReport
from stakeroute.model.recorder import ModelInteractionRecorder
from stakeroute.real.collectors import RawObservation, ingest_raw_observation
from stakeroute.real.proposal import run_proposal_cycle
from stakeroute.real.reasoners import ForecastRejection, forecast
from stakeroute.real.scopes import EvidenceAccessScope, EvidenceBundle

pytestmark = pytest.mark.anyio

TENANT_ID = "hostops"


class StubModelClient:
    def __init__(self, response: dict) -> None:
        self._response = response

    async def complete(self, purpose: str, prompt: str, timeout_s: float):
        return Accepted(value=self._response, interaction_id="raw", latency_ms=1)

    def state(self) -> ModelStateReport:
        return ModelStateReport(
            state="ok",
            detail="",
            unavailable_capabilities=(),
            calls_this_interval=0,
            ceiling=100,
        )


PROPOSAL_CASES = [
    (
        "MALFORMED_SHAPE",
        {
            "statement": "",
            "cited_observation_ids": ["e1"],
            "condition_name": None,
            "condition_params": None,
        },
    ),
    (
        "NO_CITATIONS",
        {
            "statement": "x",
            "cited_observation_ids": [],
            "condition_name": None,
            "condition_params": None,
        },
    ),
    (
        "UNKNOWN_CITATION",
        {
            "statement": "x",
            "cited_observation_ids": ["nope"],
            "condition_name": None,
            "condition_params": None,
        },
    ),
    (
        "UNKNOWN_CONDITION",
        {
            "statement": "x",
            "cited_observation_ids": ["e1"],
            "condition_name": "not_real",
            "condition_params": {},
        },
    ),
    (
        "INVALID_CONDITION_PARAMS",
        {
            "statement": "x",
            "cited_observation_ids": ["e1"],
            "condition_name": "disk_free_below",
            "condition_params": {"mount": "/"},
        },
    ),
    ("REFUSAL", {"refusal": "cannot determine a hypothesis"}),
]


@pytest.mark.parametrize("expected_reason,response", PROPOSAL_CASES)
async def test_each_proposal_rejection_reason_is_recorded_and_causes_no_effect(
    real_repo, expected_reason, response
) -> None:
    now_ms = 1_000_000
    raw = RawObservation(
        "cpu:1", now_ms - 1000, {"metric": "cpu_pct", "value": 90}, 0.9
    )
    _, snap = ingest_raw_observation(
        real_repo, TENANT_ID, "host.metrics", raw, "/x", "x"
    )
    real_repo.commit()

    # UNKNOWN_CITATION/UNKNOWN_CONDITION cases reference "e1" which must
    # resolve to the real snapshot's id for the other checks to line up.
    if "e1" in response.get("cited_observation_ids", []):
        response = {**response, "cited_observation_ids": [snap.event_id]}

    model = ModelInteractionRecorder(
        real_repo, StubModelClient(response), TENANT_ID, "real", "stub"
    )
    result = await run_proposal_cycle(
        real_repo, TENANT_ID, model, (snap,), now_ms - 10_000, now_ms, now_ms, 5.0
    )

    assert result.status == "rejected"
    assert result.rejection_reason == expected_reason
    assert real_repo.list_hypotheses(TENANT_ID) == []
    assert real_repo.list_proposals(TENANT_ID) == []

    interactions = real_repo.list_model_interactions(TENANT_ID)
    assert len(interactions) == 1
    assert interactions[0]["accepted"] == 0
    assert interactions[0]["rejection_reason"] == expected_reason


def _bundle() -> EvidenceBundle:
    scope = EvidenceAccessScope(
        agent_id="host-reasoner", source_ids=frozenset({"host.metrics"}), label="host"
    )
    obs = ObservationSnapshot(
        event_id="e1", source="host.metrics", observed_at_ms=0, payload={}, severity=0.0
    )
    return EvidenceBundle(
        agent_id="host-reasoner",
        hypothesis_id="h1",
        hypothesis_statement="x",
        scope=scope,
        observations=(obs,),
        built_at_ms=0,
    )


FORECAST_CASES = [
    ("MALFORMED_SHAPE", {"probability": 0.5, "stake": 10, "rationale": ""}, 100),
    (
        "PROBABILITY_OUT_OF_RANGE",
        {"probability": 2.0, "stake": 10, "rationale": "x"},
        100,
    ),
    ("STAKE_OUT_OF_RANGE", {"probability": 0.5, "stake": 10000, "rationale": "x"}, 100),
    ("INSUFFICIENT_CREDITS", {"probability": 0.5, "stake": 50, "rationale": "x"}, 5),
    (
        "EVIDENCE_SCOPE_VIOLATION",
        {"probability": 0.5, "stake": 10, "rationale": "per app.logs, x"},
        100,
    ),
    ("REFUSAL", {"refusal": "insufficient evidence"}, 100),
]


@pytest.mark.parametrize("expected_reason,response,credits", FORECAST_CASES)
async def test_each_forecast_rejection_reason_is_recorded_and_causes_no_effect(
    real_repo, expected_reason, response, credits
) -> None:
    model = ModelInteractionRecorder(
        real_repo, StubModelClient(response), TENANT_ID, "real", "stub"
    )
    result = await forecast(_bundle(), model, available_credits=credits, timeout_s=5.0)

    assert isinstance(result, ForecastRejection)
    assert result.reason == expected_reason

    interactions = real_repo.list_model_interactions(TENANT_ID)
    assert len(interactions) == 1
    assert interactions[0]["accepted"] == 0
    assert interactions[0]["rejection_reason"] == expected_reason


class DegradedStateModelClient:
    """Never actually called — its ``state()`` alone must be enough to
    short-circuit into a recorded rejection before any prompt is built
    (D-020)."""

    def __init__(self, state: ModelStateReport) -> None:
        self._reported_state = state

    async def complete(self, purpose: str, prompt: str, timeout_s: float):
        raise AssertionError(
            "must not be called once state() reports degraded capacity"
        )

    def state(self) -> ModelStateReport:
        return self._reported_state


DEGRADED_STATE_CASES = [
    (
        ModelStateReport(
            state="ceiling_reached",
            detail="60/60 calls this hour",
            unavailable_capabilities=("hypothesis_proposal", "prose_explanation"),
            calls_this_interval=60,
            ceiling=60,
        ),
        "CEILING_REACHED",
    ),
    (
        ModelStateReport(
            state="unconfigured",
            detail="no model configured",
            unavailable_capabilities=("hypothesis_proposal", "prose_explanation"),
            calls_this_interval=0,
            ceiling=0,
        ),
        "MODEL_DISABLED",
    ),
]


@pytest.mark.parametrize("reported_state,expected_reason", DEGRADED_STATE_CASES)
async def test_degraded_model_state_short_circuits_proposal_before_any_call(
    real_repo, reported_state, expected_reason
) -> None:
    now_ms = 1_000_000
    raw = RawObservation(
        "cpu:1", now_ms - 1000, {"metric": "cpu_pct", "value": 90}, 0.9
    )
    _, snap = ingest_raw_observation(
        real_repo, TENANT_ID, "host.metrics", raw, "/x", "x"
    )
    real_repo.commit()

    model = ModelInteractionRecorder(
        real_repo, DegradedStateModelClient(reported_state), TENANT_ID, "real", "stub"
    )
    result = await run_proposal_cycle(
        real_repo, TENANT_ID, model, (snap,), now_ms - 10_000, now_ms, now_ms, 5.0
    )

    assert result.status == "rejected"
    assert result.rejection_reason == expected_reason
    interactions = real_repo.list_model_interactions(TENANT_ID)
    assert len(interactions) == 1
    assert interactions[0]["rejection_reason"] == expected_reason
    assert interactions[0]["request"] == ""


@pytest.mark.parametrize("reported_state,expected_reason", DEGRADED_STATE_CASES)
async def test_degraded_model_state_short_circuits_forecast_before_any_call(
    real_repo, reported_state, expected_reason
) -> None:
    model = ModelInteractionRecorder(
        real_repo, DegradedStateModelClient(reported_state), TENANT_ID, "real", "stub"
    )
    result = await forecast(_bundle(), model, available_credits=100, timeout_s=5.0)

    assert isinstance(result, ForecastRejection)
    assert result.reason == expected_reason
    interactions = real_repo.list_model_interactions(TENANT_ID)
    assert len(interactions) == 1
    assert interactions[0]["rejection_reason"] == expected_reason
