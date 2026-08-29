"""SC-003: 20 correlated-evidence reports move the aggregate by ≤ 5pp.

Quickstart V3.
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


def test_correlated_evidence_moves_probability_by_at_most_five_points(client) -> None:
    client.post("/api/scenario/run_normal", json={"seed": 42})

    before = client.get("/api/hypotheses/h-database-saturation/explain").json()
    before_probability = before["aggregated_probability"]

    response = client.post(
        "/api/scenario/inject_correlated",
        json={
            "count": 20,
            "cluster": "database-observability",
            "target": "h-database-saturation",
        },
    )
    assert response.status_code == 200

    after = client.get("/api/hypotheses/h-database-saturation/explain").json()
    after_probability = after["aggregated_probability"]

    delta = abs(after_probability - before_probability)
    assert delta <= 0.05, f"aggregate moved {delta:.4f}, expected <= 0.05"

    # The 20 injected forecasts must all be visible as one discounted
    # cluster, not as 20 independent confirmations.
    correlated_contributions = [
        c
        for c in after["contributions"]
        if c["evidence_cluster_id"] == "database-observability"
    ]
    assert len(correlated_contributions) == 20
    assert all(c["cluster_size"] == 20 for c in correlated_contributions)
