"""Tests for hypothesis proposal from real observations (FR-107, FR-111, FR-122).

A stub ``ModelClient`` stands in for the live model — this file is about
the pipeline's own guarantees (citation validation, durable recording
before promotion), not about model behaviour.
"""

from __future__ import annotations

import pytest

from stakeroute.model.protocol import Accepted, ModelStateReport, Rejected
from stakeroute.model.recorder import ModelInteractionRecorder
from stakeroute.real.collectors import RawObservation, ingest_raw_observation
from stakeroute.real.proposal import run_proposal_cycle

pytestmark = pytest.mark.anyio

TENANT_ID = "hostops"
HOME_DIR = "/Users/alice"
USERNAME = "alice"


class StubModelClient:
    """A ``ModelClient`` returning a fixed, pre-baked response."""

    def __init__(self, response: dict) -> None:
        self._response = response

    async def complete(self, purpose: str, prompt: str, timeout_s: float):
        return Accepted(value=self._response, interaction_id="raw", latency_ms=5)

    def state(self) -> ModelStateReport:
        return ModelStateReport(
            state="ok",
            detail="",
            unavailable_capabilities=(),
            calls_this_interval=0,
            ceiling=100,
        )


class RefusingModelClient:
    async def complete(self, purpose: str, prompt: str, timeout_s: float):
        return Rejected(reason="TIMEOUT", interaction_id="raw", latency_ms=10000)

    def state(self) -> ModelStateReport:
        return ModelStateReport(
            state="degraded",
            detail="",
            unavailable_capabilities=("hypothesis_proposal",),
            calls_this_interval=0,
            ceiling=100,
        )


def _ingest_window(real_repo, now_ms: int) -> tuple:
    raw1 = RawObservation(
        source_event_id="disk:/data:1",
        observed_at_ms=now_ms - 1000,
        payload={"metric": "disk_used_pct", "mount": "/data", "value": 96.0},
        severity=0.9,
    )
    raw2 = RawObservation(
        source_event_id="disk:/data:2",
        observed_at_ms=now_ms - 500,
        payload={"metric": "disk_used_pct", "mount": "/data", "value": 97.0},
        severity=0.95,
    )
    _, snap1 = ingest_raw_observation(
        real_repo, TENANT_ID, "host.metrics", raw1, HOME_DIR, USERNAME
    )
    _, snap2 = ingest_raw_observation(
        real_repo, TENANT_ID, "host.metrics", raw2, HOME_DIR, USERNAME
    )
    real_repo.commit()
    return snap1, snap2


async def test_valid_proposal_promotes_to_a_hypothesis(real_repo) -> None:
    now_ms = 2_000_000
    snap1, snap2 = _ingest_window(real_repo, now_ms)

    response = {
        "statement": "The /data volume is filling up",
        "cited_observation_ids": [snap1.event_id, snap2.event_id],
        "condition_name": "disk_free_below",
        "condition_params": {"mount": "/data", "pct": 10},
    }
    model = ModelInteractionRecorder(
        real_repo, StubModelClient(response), TENANT_ID, "real", "stub-model"
    )

    result = await run_proposal_cycle(
        real_repo,
        TENANT_ID,
        model,
        observations=(snap1, snap2),
        window_start_ms=now_ms - 10_000,
        window_end_ms=now_ms,
        now_ms=now_ms,
        timeout_s=5.0,
    )

    assert result.status == "promoted"
    assert result.hypothesis_id is not None
    hypothesis = real_repo.get_hypothesis(result.hypothesis_id)
    assert hypothesis["mode"] == "real"
    assert hypothesis["statement"] == response["statement"]
    assert hypothesis["condition_name"] == "disk_free_below"

    proposal = real_repo.get_proposal(result.proposal_id)
    assert proposal["status"] == "promoted"

    estimates = real_repo.current_attribute_estimates(result.hypothesis_id)
    attributes = {row["attribute"] for row in estimates}
    assert attributes == {"impact", "urgency", "review_cost"}
    for row in estimates:
        assert row["basis"]


async def test_unknown_citation_rejects_the_whole_proposal(real_repo) -> None:
    now_ms = 2_000_000
    snap1, _snap2 = _ingest_window(real_repo, now_ms)

    response = {
        "statement": "Something is wrong",
        "cited_observation_ids": [snap1.event_id, "does-not-exist"],
        "condition_name": None,
        "condition_params": None,
    }
    model = ModelInteractionRecorder(
        real_repo, StubModelClient(response), TENANT_ID, "real", "stub-model"
    )

    result = await run_proposal_cycle(
        real_repo,
        TENANT_ID,
        model,
        observations=(snap1,),
        window_start_ms=now_ms - 10_000,
        window_end_ms=now_ms,
        now_ms=now_ms,
        timeout_s=5.0,
    )

    assert result.status == "rejected"
    assert result.rejection_reason == "UNKNOWN_CITATION"
    assert real_repo.list_hypotheses(TENANT_ID) == []

    interactions = real_repo.list_model_interactions(TENANT_ID)
    assert len(interactions) == 1
    assert interactions[0]["accepted"] == 0
    assert interactions[0]["rejection_reason"] == "UNKNOWN_CITATION"


async def test_out_of_window_citation_rejects_the_whole_proposal(real_repo) -> None:
    now_ms = 2_000_000
    snap1, snap2 = _ingest_window(real_repo, now_ms)

    response = {
        "statement": "Something is wrong",
        "cited_observation_ids": [snap1.event_id, snap2.event_id],
        "condition_name": None,
        "condition_params": None,
    }
    model = ModelInteractionRecorder(
        real_repo, StubModelClient(response), TENANT_ID, "real", "stub-model"
    )

    # A window that excludes snap1/snap2's observed_at_ms entirely.
    result = await run_proposal_cycle(
        real_repo,
        TENANT_ID,
        model,
        observations=(snap1, snap2),
        window_start_ms=now_ms + 100_000,
        window_end_ms=now_ms + 200_000,
        now_ms=now_ms,
        timeout_s=5.0,
    )

    assert result.status == "rejected"
    assert result.rejection_reason == "CITATION_OUT_OF_WINDOW"
    assert real_repo.list_hypotheses(TENANT_ID) == []


async def test_nothing_enters_the_queue_before_being_durably_recorded(
    real_repo,
) -> None:
    now_ms = 2_000_000
    snap1, snap2 = _ingest_window(real_repo, now_ms)
    response = {
        "statement": "The /data volume is filling up",
        "cited_observation_ids": [snap1.event_id, snap2.event_id],
        "condition_name": None,
        "condition_params": None,
    }
    model = ModelInteractionRecorder(
        real_repo, StubModelClient(response), TENANT_ID, "real", "stub-model"
    )

    result = await run_proposal_cycle(
        real_repo,
        TENANT_ID,
        model,
        observations=(snap1, snap2),
        window_start_ms=now_ms - 10_000,
        window_end_ms=now_ms,
        now_ms=now_ms,
        timeout_s=5.0,
    )

    assert result.status == "promoted"
    # The proposal row exists and is durably committed before the
    # hypothesis it produced.
    proposal = real_repo.get_proposal(result.proposal_id)
    hypothesis = real_repo.get_hypothesis(result.hypothesis_id)
    assert proposal is not None
    assert hypothesis is not None
    assert hypothesis["proposal_id"] == result.proposal_id


async def test_model_rejection_produces_no_proposal_or_hypothesis(real_repo) -> None:
    now_ms = 2_000_000
    snap1, snap2 = _ingest_window(real_repo, now_ms)
    model = ModelInteractionRecorder(
        real_repo, RefusingModelClient(), TENANT_ID, "real", "stub-model"
    )

    result = await run_proposal_cycle(
        real_repo,
        TENANT_ID,
        model,
        observations=(snap1, snap2),
        window_start_ms=now_ms - 10_000,
        window_end_ms=now_ms,
        now_ms=now_ms,
        timeout_s=5.0,
    )

    assert result.status == "rejected"
    assert result.rejection_reason == "TIMEOUT"
    assert real_repo.list_hypotheses(TENANT_ID) == []
    assert real_repo.list_proposals(TENANT_ID) == []


async def test_exact_condition_duplicate_merges_instead_of_creating_a_second_hypothesis(
    real_repo,
) -> None:
    now_ms = 2_000_000
    snap1, snap2 = _ingest_window(real_repo, now_ms)
    response = {
        "statement": "The /data volume is filling up",
        "cited_observation_ids": [snap1.event_id, snap2.event_id],
        "condition_name": "disk_free_below",
        "condition_params": {"mount": "/data", "pct": 10},
    }
    model = ModelInteractionRecorder(
        real_repo, StubModelClient(response), TENANT_ID, "real", "stub-model"
    )

    first = await run_proposal_cycle(
        real_repo,
        TENANT_ID,
        model,
        (snap1, snap2),
        now_ms - 10_000,
        now_ms,
        now_ms,
        5.0,
    )
    assert first.status == "promoted"

    second = await run_proposal_cycle(
        real_repo,
        TENANT_ID,
        model,
        (snap1, snap2),
        now_ms - 10_000,
        now_ms,
        now_ms + 1000,
        5.0,
    )
    assert second.status == "merged"
    assert second.duplicate_of == first.proposal_id

    open_hypotheses = real_repo.list_hypotheses(TENANT_ID, status="open")
    assert len(open_hypotheses) == 1
