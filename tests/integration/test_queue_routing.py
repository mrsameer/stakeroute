"""SC-001, SC-004: baseline routing via the HTTP API (quickstart V1)."""

import uuid

import pytest
from fastapi.testclient import TestClient

from stakeroute.config import ATTENTION_BUDGET


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / f"{uuid.uuid4().hex}.db")
    monkeypatch.setenv("STAKEROUTE_TEST_DB_PATH", db_path)
    import stakeroute.dashboard.main as dashboard_main

    monkeypatch.setattr(dashboard_main, "DB_PATH", db_path)
    dashboard_main._repo = None
    return TestClient(dashboard_main.app)


def test_run_normal_routes_exactly_budget_with_payment_incident_first(client) -> None:
    response = client.post("/api/scenario/run_normal", json={"seed": 42})
    assert response.status_code == 200
    body = response.json()
    assert body["routed"] == ATTENTION_BUDGET
    assert body["withheld_count"] > 0

    queue = client.get("/api/queue").json()
    assert queue["attention_budget"] == ATTENTION_BUDGET
    assert queue["slots_used"] == ATTENTION_BUDGET
    assert queue["withheld_count"] == body["withheld_count"]
    assert len(queue["routed"]) == ATTENTION_BUDGET
    assert queue["routed"][0]["rank"] == 1
    assert queue["routed"][0]["hypothesis_id"] == "h-payment-failure"


def test_more_candidates_than_budget_are_visibly_withheld(client) -> None:
    client.post("/api/scenario/run_normal", json={"seed": 42})
    queue = client.get("/api/queue").json()
    # 5 candidate hypotheses generated (2 real + 3 minor), budget of 2.
    assert queue["withheld_count"] == 3
