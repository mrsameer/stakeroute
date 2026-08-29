"""FR-028: an expired hypothesis returns stakes exactly and leaves
reputation unchanged. No outcome is inferred.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from stakeroute.worker.settlement_runner import expire_overdue_hypotheses


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / f"{uuid.uuid4().hex}.db")
    import stakeroute.dashboard.main as dashboard_main

    monkeypatch.setattr(dashboard_main, "DB_PATH", db_path)
    dashboard_main._repo = None
    return TestClient(dashboard_main.app)


def test_expired_hypothesis_returns_stakes_and_leaves_reputation_unchanged(
    client,
) -> None:
    client.post("/api/scenario/run_normal", json={"seed": 42})

    import stakeroute.dashboard.main as dashboard_main

    repo = dashboard_main.get_repo()
    tenant_id = "acmepay"

    forecasts_before = repo.list_forecasts_for_hypothesis("h-payment-failure")
    assert forecasts_before

    before_agents = {}
    for forecast in forecasts_before:
        agent_row = repo.get_agent(forecast["agent_id"])
        assert agent_row is not None
        before_agents[forecast["agent_id"]] = agent_row

    # Force the hypothesis into the past so it is eligible for expiry.
    repo._conn.execute(
        "UPDATE hypotheses SET deadline_ms = 0 WHERE id = 'h-payment-failure'"
    )
    repo.commit()

    expired_ids = expire_overdue_hypotheses(
        repo, tenant_id, now_ms=dashboard_main.now_ms()
    )
    assert "h-payment-failure" in expired_ids

    hypothesis = repo.get_hypothesis("h-payment-failure")
    assert hypothesis is not None
    assert hypothesis["status"] == "expired"

    # No outcome was inferred.
    assert repo.list_settlements_for_hypothesis("h-payment-failure") == []

    for forecast in forecasts_before:
        agent_id = forecast["agent_id"]
        before = before_agents[agent_id]
        after = repo.get_agent(agent_id)
        assert after is not None

        # Reputation untouched.
        assert after["reputation"] == before["reputation"]

        # The stake on THIS forecast came back in full. Other agents may
        # have forecasts on other still-open hypotheses, so compare the
        # delta rather than the absolute balance.
        assert (
            after["available_credits"]
            == before["available_credits"] + forecast["stake"]
        )
        assert after["staked_credits"] == before["staked_credits"] - forecast["stake"]
