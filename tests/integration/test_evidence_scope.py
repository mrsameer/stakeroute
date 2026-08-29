"""Tests for evidence-scope enforcement (FR-114, FR-116, SC-102).

An agent's bundle contains only sources inside its declared scope, and a
rationale that reaches outside that scope is rejected rather than
accepted with a note.
"""

from __future__ import annotations

import json

import pytest

from stakeroute.model.protocol import Accepted, ModelStateReport
from stakeroute.model.recorder import ModelInteractionRecorder
from stakeroute.real.collectors import RawObservation, ingest_raw_observation
from stakeroute.real.reasoners import run_agent_forecast
from stakeroute.real.scopes import EvidenceAccessScope, build_evidence_bundle

pytestmark = pytest.mark.anyio

TENANT_ID = "hostops"
HOME_DIR = "/Users/x"
USERNAME = "x"

_BANNED_TOKENS = ("outcome", "ground_truth", "groundtruth", "accuracy")


class StubModelClient:
    def __init__(self, response: dict) -> None:
        self._response = response

    async def complete(self, purpose: str, prompt: str, timeout_s: float):
        return Accepted(value=self._response, interaction_id="raw", latency_ms=2)

    def state(self) -> ModelStateReport:
        return ModelStateReport(
            state="ok",
            detail="",
            unavailable_capabilities=(),
            calls_this_interval=0,
            ceiling=100,
        )


async def _seed(real_repo, now_ms: int) -> None:
    real_repo.upsert_agent(
        agent_id="host-reasoner",
        tenant_id=TENANT_ID,
        display_name="Host Reasoner",
        reputation=0.5,
        available_credits=100,
        staked_credits=0,
        attested=True,
        created_at_ms=0,
    )
    real_repo.upsert_hypothesis(
        hypothesis_id="h1",
        tenant_id=TENANT_ID,
        statement="CPU is saturated",
        prior_probability=0.5,
        impact_minor_units=1,
        urgency=0.5,
        review_cost=1.0,
        deadline_ms=now_ms + 60_000,
        status="open",
        created_at_ms=now_ms,
    )
    ingest_raw_observation(
        real_repo,
        TENANT_ID,
        "host.metrics",
        RawObservation("cpu:1", now_ms - 1000, {"metric": "cpu_pct", "value": 95}, 0.9),
        HOME_DIR,
        USERNAME,
    )
    ingest_raw_observation(
        real_repo,
        TENANT_ID,
        "app.logs",
        RawObservation(
            "log:1",
            now_ms - 1000,
            {"level": "ERROR", "logger": "x", "message": "disk full"},
            0.7,
        ),
        HOME_DIR,
        USERNAME,
    )
    real_repo.commit()


async def test_bundle_contains_only_in_scope_sources(real_repo) -> None:
    now_ms = 1_000_000
    await _seed(real_repo, now_ms)
    scope = EvidenceAccessScope(
        agent_id="host-reasoner", source_ids=frozenset({"host.metrics"}), label="host"
    )
    bundle = build_evidence_bundle(
        real_repo, TENANT_ID, scope, "h1", "CPU is saturated", 0, now_ms, now_ms
    )
    assert bundle.observations
    assert all(o.source == "host.metrics" for o in bundle.observations)
    assert not any(o.source == "app.logs" for o in bundle.observations)


async def test_rationale_citing_out_of_scope_evidence_is_rejected(real_repo) -> None:
    now_ms = 1_000_000
    await _seed(real_repo, now_ms)
    scope = EvidenceAccessScope(
        agent_id="host-reasoner", source_ids=frozenset({"host.metrics"}), label="host"
    )
    bundle = build_evidence_bundle(
        real_repo, TENANT_ID, scope, "h1", "CPU is saturated", 0, now_ms, now_ms
    )
    model = ModelInteractionRecorder(
        real_repo,
        StubModelClient(
            {
                "probability": 0.8,
                "stake": 10,
                "rationale": (
                    "combined with app.logs showing repeated failures, "
                    "this is severe"
                ),
            }
        ),
        TENANT_ID,
        "real",
        "stub",
    )
    result = await run_agent_forecast(
        real_repo, TENANT_ID, bundle, model, now_ms, 5.0, now_ms + 60_000
    )
    assert result is None
    rejected = real_repo.list_rejected_forecasts(TENANT_ID)
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "EVIDENCE_SCOPE_VIOLATION"


async def test_every_recorded_bundle_is_outcome_free(real_repo) -> None:
    now_ms = 1_000_000
    await _seed(real_repo, now_ms)
    scope = EvidenceAccessScope(
        agent_id="host-reasoner", source_ids=frozenset({"host.metrics"}), label="host"
    )
    bundle = build_evidence_bundle(
        real_repo, TENANT_ID, scope, "h1", "CPU is saturated", 0, now_ms, now_ms
    )
    model = ModelInteractionRecorder(
        real_repo,
        StubModelClient(
            {"probability": 0.8, "stake": 10, "rationale": "cpu is pinned at 95%"}
        ),
        TENANT_ID,
        "real",
        "stub",
    )
    result = await run_agent_forecast(
        real_repo, TENANT_ID, bundle, model, now_ms, 5.0, now_ms + 60_000
    )
    assert result is not None

    forecasts = real_repo.list_forecasts_for_hypothesis("h1")
    assert forecasts
    for row in forecasts:
        assert row["evidence_bundle"] is not None
        bundle_json = json.dumps(json.loads(row["evidence_bundle"])).lower()
        for token in _BANNED_TOKENS:
            assert token not in bundle_json, f"bundle leaked {token!r}: {bundle_json}"
