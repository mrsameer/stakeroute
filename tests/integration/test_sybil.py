"""SC-002: 50 Sybil agents flip majority vote but not StakeRoute.

The money moment — quickstart V2.
"""

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / f"{uuid.uuid4().hex}.db")
    import stakeroute.dashboard.main as dashboard_main

    monkeypatch.setattr(dashboard_main, "DB_PATH", db_path)
    dashboard_main._repo = None
    return TestClient(dashboard_main.app)


def test_sybil_flood_flips_majority_vote_but_not_stakeroute(client) -> None:
    client.post("/api/scenario/run_normal", json={"seed": 42})

    before = client.get("/api/comparison").json()
    assert before["strategies"]["stakeroute"][0]["hypothesis_id"] == "h-payment-failure"

    response = client.post(
        "/api/scenario/inject_sybils",
        json={"count": 50, "target": "h-database-saturation"},
    )
    assert response.status_code == 200

    after = client.get("/api/comparison").json()

    majority_top = after["strategies"]["majority_vote"][0]["hypothesis_id"]
    assert majority_top == "h-database-saturation"

    stakeroute_top_two = {
        row["hypothesis_id"] for row in after["strategies"]["stakeroute"][:2]
    }
    assert "h-payment-failure" in stakeroute_top_two

    assert after["ground_truth"]["h-payment-failure"] == 1
    assert after["ground_truth"]["h-database-saturation"] == 0
