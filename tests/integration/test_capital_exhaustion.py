"""US2 acceptance scenario 3: an agent staking maximally on every signal is
rejected once its epoch budget is spent.
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from stakeroute.config import EPOCH_GRANT, STAKE_MAX
from stakeroute.core.types import compute_event_id
from stakeroute.worker.main import (
    FORECASTS_CREATED,
    consume_once,
    handle_forecast_created,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / f"{uuid.uuid4().hex}.db")
    import stakeroute.dashboard.main as dashboard_main

    monkeypatch.setattr(dashboard_main, "DB_PATH", db_path)
    dashboard_main._repo = None
    return TestClient(dashboard_main.app)


async def _attempt_forecast(
    dashboard_main, repo, agent_id: str, hypothesis_id: str, seq: int
) -> None:
    tenant_id = "acmepay"
    now = dashboard_main.now_ms() + seq
    source_event_id = f"{agent_id}-{hypothesis_id}-attempt-{seq}"
    event_id = compute_event_id(tenant_id, "forecasts", source_event_id, now)
    await dashboard_main._transport.publish(
        FORECASTS_CREATED,
        {
            "tenant_id": tenant_id,
            "event_id": event_id,
            "emitted_at_ms": now,
            "payload": {
                "hypothesis_id": hypothesis_id,
                "agent_id": agent_id,
                "probability": 0.5,
                "stake": STAKE_MAX,
                "evidence_cluster_id": f"exhaustion-{seq}",
                "evidence_refs": [],
                "source_event_id": source_event_id,
                "expires_at_ms": now + 3_600_000,
            },
        },
    )
    await consume_once(
        dashboard_main._transport,
        FORECASTS_CREATED,
        "dashboard",
        repo,
        tenant_id,
        handle_forecast_created,
    )


def test_repeated_max_stake_forecasts_exhaust_the_epoch_budget(client) -> None:
    client.post("/api/scenario/run_normal", json={"seed": 42})

    import stakeroute.dashboard.main as dashboard_main

    repo = dashboard_main.get_repo()
    agent_id = "payment-agent-1"

    agent = repo.get_agent(agent_id)
    assert agent is not None
    # The baseline scenario already used this agent on two real hypotheses,
    # so available credits are somewhere under the full epoch grant.
    assert 0 < agent["available_credits"] <= EPOCH_GRANT

    # Repeatedly stake this agent at maximum on THREE DISTINCT new
    # hypotheses (a resubmission on the same hypothesis replaces rather
    # than accumulates, per FR-044, so distinct targets are what actually
    # exercises exhaustion). 3 * STAKE_MAX comfortably exceeds any
    # remaining balance after the baseline scenario's own two forecasts.
    targets = ["h-exhaustion-0", "h-exhaustion-1", "h-exhaustion-2"]
    for hypothesis_id in targets:
        repo.upsert_hypothesis(
            hypothesis_id=hypothesis_id,
            tenant_id="acmepay",
            statement=hypothesis_id,
            prior_probability=0.2,
            impact_minor_units=1_000_000,
            urgency=0.5,
            review_cost=1.0,
            deadline_ms=dashboard_main.now_ms() + 3_600_000,
            status="open",
            created_at_ms=dashboard_main.now_ms(),
        )
    repo.commit()

    accepted_count = 0
    for seq, hypothesis_id in enumerate(targets):
        before = repo.get_forecast(f"forecast-{hypothesis_id}-{agent_id}")
        asyncio.run(
            _attempt_forecast(dashboard_main, repo, agent_id, hypothesis_id, seq)
        )
        after = repo.get_forecast(f"forecast-{hypothesis_id}-{agent_id}")
        if before is None and after is not None:
            accepted_count += 1

    final_agent = repo.get_agent(agent_id)
    assert final_agent is not None
    assert final_agent["available_credits"] >= 0
    # 3 attempts at STAKE_MAX cannot all have been accepted against a
    # remaining balance well under 3 * STAKE_MAX — the budget is finite.
    assert accepted_count < len(targets)
