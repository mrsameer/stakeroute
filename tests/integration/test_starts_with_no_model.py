"""FR-126, SC-113: the system must start with no model configured at all
and still run — collectors ingest, ranking passes complete, settlement
works, and the queue endpoint is well-formed. An empty queue is correct
here (no proposals without a model); the mechanism must be fully alive.
"""

from __future__ import annotations

import pytest

from stakeroute.model.null import NullModelClient
from stakeroute.model.recorder import ModelInteractionRecorder
from stakeroute.real.collectors import RawObservation, ingest_raw_observation
from stakeroute.real.proposal import run_proposal_cycle
from stakeroute.real.reasoners import ForecastRejection, forecast
from stakeroute.real.scopes import EvidenceAccessScope, EvidenceBundle

pytestmark = pytest.mark.anyio

TENANT_ID = "hostops"


async def test_proposal_cycle_with_null_model_rejects_cleanly(real_repo) -> None:
    now_ms = 1_000_000
    raw = RawObservation(
        "cpu:1", now_ms - 1000, {"metric": "cpu_pct", "value": 95}, 0.9
    )
    _, snap = ingest_raw_observation(
        real_repo, TENANT_ID, "host.metrics", raw, "/x", "x"
    )
    real_repo.commit()

    model = ModelInteractionRecorder(
        real_repo, NullModelClient(), TENANT_ID, "real", "none"
    )
    result = await run_proposal_cycle(
        real_repo, TENANT_ID, model, (snap,), now_ms - 10_000, now_ms, now_ms, 5.0
    )

    assert result.status == "rejected"
    assert result.rejection_reason == "MODEL_DISABLED"
    assert real_repo.list_hypotheses(TENANT_ID) == []


async def test_forecast_with_null_model_rejects_cleanly(real_repo) -> None:
    scope = EvidenceAccessScope(
        agent_id="host-reasoner", source_ids=frozenset({"host.metrics"}), label="host"
    )
    bundle = EvidenceBundle(
        agent_id="host-reasoner",
        hypothesis_id="h1",
        hypothesis_statement="x",
        scope=scope,
        observations=(),
        built_at_ms=0,
    )
    model = ModelInteractionRecorder(
        real_repo, NullModelClient(), TENANT_ID, "real", "none"
    )
    result = await forecast(bundle, model, available_credits=100, timeout_s=5.0)
    assert isinstance(result, ForecastRejection)
    assert result.reason == "MODEL_DISABLED"


def test_mode_endpoint_reports_unconfigured_with_no_model(monkeypatch) -> None:
    monkeypatch.setattr("stakeroute.dashboard.main.STAKEROUTE_MODEL", "none")
    from fastapi.testclient import TestClient

    from stakeroute.dashboard import main as dashboard_main

    dashboard_main._repo = None
    client = TestClient(dashboard_main.app)

    response = client.get("/api/mode", params={"tenant": "hostops"})
    assert response.status_code == 200
    body = response.json()
    assert body["model"]["state"] == "unconfigured"
    assert set(body["model"]["unavailable_capabilities"]) == {
        "hypothesis_proposal",
        "prose_explanation",
    }


def test_queue_and_agents_endpoints_are_well_formed_with_no_real_data(
    monkeypatch,
) -> None:
    monkeypatch.setattr("stakeroute.dashboard.main.STAKEROUTE_MODEL", "none")
    from fastapi.testclient import TestClient

    from stakeroute.dashboard import main as dashboard_main

    dashboard_main._repo = None
    client = TestClient(dashboard_main.app)

    queue_response = client.get("/api/queue", params={"tenant": "hostops"})
    assert queue_response.status_code == 200
    assert queue_response.json()["routed"] == []

    agents_response = client.get("/api/agents", params={"tenant": "hostops"})
    assert agents_response.status_code == 200
    assert agents_response.json()["agents"] == []
