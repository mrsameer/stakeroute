"""SC-008, SC-009: settlement moves credits/reputation in the right
direction, and no loss exceeds its stake — quickstart V5.
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


def test_settlement_moves_credits_and_reputation_in_the_right_direction(client) -> None:
    client.post("/api/scenario/run_normal", json={"seed": 42})

    import stakeroute.dashboard.main as dashboard_main

    repo = dashboard_main.get_repo()

    before_agents = {row["id"]: dict(row) for row in repo.list_agents("acmepay")}

    response = client.post(
        "/api/scenario/resolve",
        json={"hypothesis_id": "h-payment-failure", "outcome": 1},
    )
    assert response.status_code == 200

    hypothesis = repo.get_hypothesis("h-payment-failure")
    assert hypothesis is not None
    assert hypothesis["status"] == "resolved"

    settlements = repo.list_settlements_for_hypothesis("h-payment-failure")
    assert settlements

    for settlement in settlements:
        forecast = repo.get_forecast(settlement["forecast_id"])
        assert forecast is not None
        agent_id = forecast["agent_id"]
        before = before_agents[agent_id]

        # SC-009: no loss exceeds the amount staked.
        assert settlement["credit_delta"] >= -forecast["stake"]

        # SC-008: direction of movement follows improvement over the prior.
        if settlement["improvement"] > 0:
            assert settlement["credit_delta"] >= 0
            assert settlement["reputation_after"] >= before["reputation"]
        elif settlement["improvement"] < 0:
            assert settlement["credit_delta"] <= 0
            assert settlement["reputation_after"] <= before["reputation"]

    # Duplicate-settlement check from quickstart V5 — zero rows expected.
    assert repo.duplicate_settlement_count() == 0


def test_resolve_is_idempotent_on_replay(client) -> None:
    client.post("/api/scenario/run_normal", json={"seed": 42})
    client.post(
        "/api/scenario/resolve",
        json={"hypothesis_id": "h-payment-failure", "outcome": 1},
    )

    import stakeroute.dashboard.main as dashboard_main

    repo = dashboard_main.get_repo()
    balances_after_first = {
        row["id"]: row["available_credits"] for row in repo.list_agents("acmepay")
    }

    # A second resolve for the same hypothesis must be a no-op.
    client.post(
        "/api/scenario/resolve",
        json={"hypothesis_id": "h-payment-failure", "outcome": 1},
    )
    balances_after_second = {
        row["id"]: row["available_credits"] for row in repo.list_agents("acmepay")
    }

    assert balances_after_first == balances_after_second
    assert repo.duplicate_settlement_count() == 0
