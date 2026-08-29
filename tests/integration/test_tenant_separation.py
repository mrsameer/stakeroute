"""Tests for real/simulated tenant separation (SC-117, FR-147, D-018).

Aggregating ``hostops`` with ``acmepay`` must require new code, not a
missing filter.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from stakeroute.dashboard import main as dashboard_main
from stakeroute.real.collectors import RawObservation, ingest_raw_observation


def _fresh_client() -> TestClient:
    dashboard_main._repo = None
    dashboard_main._transport.__init__()
    return TestClient(dashboard_main.app)


def test_zero_real_mode_rows_carry_the_simulated_tenant(real_repo) -> None:
    raw = RawObservation("cpu:1", 1_000_000, {"metric": "cpu_pct", "value": 90}, 0.9)
    ingest_raw_observation(real_repo, "hostops", "host.metrics", raw, "/Users/x", "x")
    real_repo.commit()

    rows = real_repo.list_events("hostops")
    assert rows
    for row in rows:
        assert row["tenant_id"] == "hostops"
        assert row["mode"] == "real"


def test_zero_simulated_rows_carry_the_real_tenant() -> None:
    client = _fresh_client()
    client.post("/api/scenario/run_normal", json={"seed": 42})

    repo = dashboard_main.get_repo()
    for row in repo.list_events("acmepay"):
        assert row["tenant_id"] == "acmepay"
        assert row["mode"] == "sim"
    hostops_events = repo.list_events("hostops")
    assert hostops_events == []


def test_queue_endpoint_accepts_an_explicit_tenant() -> None:
    client = _fresh_client()
    client.post("/api/scenario/run_normal", json={"seed": 42})

    response = client.get("/api/queue", params={"tenant": "acmepay"})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "sim"


def test_queue_endpoint_refuses_more_than_one_tenant() -> None:
    client = _fresh_client()
    response = client.get(
        "/api/queue", params=[("tenant", "acmepay"), ("tenant", "hostops")]
    )
    assert response.status_code == 400


def test_agents_endpoint_refuses_more_than_one_tenant() -> None:
    client = _fresh_client()
    response = client.get(
        "/api/agents", params=[("tenant", "acmepay"), ("tenant", "hostops")]
    )
    assert response.status_code == 400


def test_metrics_endpoint_refuses_more_than_one_tenant() -> None:
    client = _fresh_client()
    response = client.get(
        "/api/metrics", params=[("tenant", "acmepay"), ("tenant", "hostops")]
    )
    assert response.status_code == 400


def test_hostops_queue_is_empty_when_no_real_data_has_been_ingested() -> None:
    client = _fresh_client()
    response = client.get("/api/queue", params={"tenant": "hostops"})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "real"
    assert body["routed"] == []
