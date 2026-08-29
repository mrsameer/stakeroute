"""SC-005, FR-003: redelivering every message class produces exactly one
stored effect, via the in-process transport driver.

At-least-once delivery means a message can arrive twice even without a
crash (an ACK can be lost in transit as easily as a consumer can die).
This test redelivers the identical envelope — same event_id — for each of
the three subjects and asserts the effect happened exactly once.
"""

import asyncio

from stakeroute.core.types import compute_event_id
from stakeroute.storage.repository import Repository
from stakeroute.transport.memory import MemoryTransport
from stakeroute.worker.main import (
    FORECASTS_CREATED,
    OUTCOMES_RESOLVED,
    SIGNALS_RAW,
    consume_once,
    handle_forecast_created,
    handle_outcome_resolved,
    handle_signal,
)

TENANT_ID = "acmepay"


def _repo(tmp_path) -> Repository:
    repo = Repository(str(tmp_path / "idempotency.db"))
    repo.ensure_tenant(TENANT_ID, "AcmePay", 0)
    repo.commit()
    return repo


def _seed_hypothesis_and_agent(repo: Repository) -> None:
    repo.upsert_hypothesis(
        hypothesis_id="h-1",
        tenant_id=TENANT_ID,
        statement="test hypothesis",
        prior_probability=0.3,
        impact_minor_units=1_000_000,
        urgency=1.0,
        review_cost=1.0,
        deadline_ms=10_000_000,
        status="open",
        created_at_ms=0,
    )
    repo.upsert_agent(
        agent_id="agent-1",
        tenant_id=TENANT_ID,
        display_name="Agent 1",
        reputation=0.5,
        available_credits=100,
        staked_credits=0,
        attested=True,
        created_at_ms=0,
    )
    repo.commit()


def test_redelivered_signal_produces_one_event_row(tmp_path) -> None:
    repo = _repo(tmp_path)
    transport = MemoryTransport()
    envelope = {
        "tenant_id": TENANT_ID,
        "event_id": compute_event_id(TENANT_ID, "logs", "sig-1", 1000),
        "emitted_at_ms": 1000,
        "payload": {
            "source": "logs",
            "source_event_id": "sig-1",
            "provenance": {},
            "payload": {"metric": "x", "value": 1},
        },
    }

    async def run() -> None:
        await transport.publish(SIGNALS_RAW, envelope)
        await consume_once(
            transport, SIGNALS_RAW, "worker", repo, TENANT_ID, handle_signal
        )
        # Redeliver the identical envelope.
        await transport.publish(SIGNALS_RAW, envelope)
        await consume_once(
            transport, SIGNALS_RAW, "worker", repo, TENANT_ID, handle_signal
        )

    asyncio.run(run())

    assert repo.count_events(TENANT_ID) == 1


def test_redelivered_forecast_produces_one_stake_lock(tmp_path) -> None:
    repo = _repo(tmp_path)
    _seed_hypothesis_and_agent(repo)
    transport = MemoryTransport()
    envelope = {
        "tenant_id": TENANT_ID,
        "event_id": compute_event_id(TENANT_ID, "forecasts", "fc-1", 2000),
        "emitted_at_ms": 2000,
        "payload": {
            "hypothesis_id": "h-1",
            "agent_id": "agent-1",
            "probability": 0.8,
            "stake": 30,
            "evidence_cluster_id": "cluster-1",
            "evidence_refs": [],
            "source_event_id": "fc-1",
            "expires_at_ms": 999_999_999,
        },
    }

    async def run() -> None:
        await transport.publish(FORECASTS_CREATED, envelope)
        await consume_once(
            transport,
            FORECASTS_CREATED,
            "worker",
            repo,
            TENANT_ID,
            handle_forecast_created,
        )
        await transport.publish(FORECASTS_CREATED, envelope)
        await consume_once(
            transport,
            FORECASTS_CREATED,
            "worker",
            repo,
            TENANT_ID,
            handle_forecast_created,
        )

    asyncio.run(run())

    agent = repo.get_agent("agent-1")
    assert agent is not None
    # Stake locked exactly once: 100 - 30 = 70, not 100 - 60 = 40.
    assert agent["available_credits"] == 70
    assert agent["staked_credits"] == 30
    assert repo.count_events(TENANT_ID) == 1


def test_redelivered_outcome_produces_one_settlement_per_forecast(tmp_path) -> None:
    repo = _repo(tmp_path)
    _seed_hypothesis_and_agent(repo)
    transport = MemoryTransport()

    forecast_envelope = {
        "tenant_id": TENANT_ID,
        "event_id": compute_event_id(TENANT_ID, "forecasts", "fc-1", 2000),
        "emitted_at_ms": 2000,
        "payload": {
            "hypothesis_id": "h-1",
            "agent_id": "agent-1",
            "probability": 0.8,
            "stake": 30,
            "evidence_cluster_id": "cluster-1",
            "evidence_refs": [],
            "source_event_id": "fc-1",
            "expires_at_ms": 999_999_999,
        },
    }
    outcome_envelope = {
        "tenant_id": TENANT_ID,
        "event_id": compute_event_id(TENANT_ID, "outcomes", "h-1", 3000),
        "emitted_at_ms": 3000,
        "payload": {
            "hypothesis_id": "h-1",
            "outcome": 1,
            "resolved_by": "operator",
            "resolved_at_ms": 3000,
        },
    }

    async def run() -> None:
        await transport.publish(FORECASTS_CREATED, forecast_envelope)
        await consume_once(
            transport,
            FORECASTS_CREATED,
            "worker",
            repo,
            TENANT_ID,
            handle_forecast_created,
        )
        await transport.publish(OUTCOMES_RESOLVED, outcome_envelope)
        await consume_once(
            transport,
            OUTCOMES_RESOLVED,
            "worker",
            repo,
            TENANT_ID,
            handle_outcome_resolved,
        )
        # Redeliver the identical resolution.
        await transport.publish(OUTCOMES_RESOLVED, outcome_envelope)
        await consume_once(
            transport,
            OUTCOMES_RESOLVED,
            "worker",
            repo,
            TENANT_ID,
            handle_outcome_resolved,
        )

    asyncio.run(run())

    settlements = repo.list_settlements_for_hypothesis("h-1")
    assert len(settlements) == 1
    assert repo.duplicate_settlement_count() == 0

    # Balance reflects exactly one settlement's effect.
    agent = repo.get_agent("agent-1")
    assert agent is not None
    expected_available = 70 + 30 + settlements[0]["credit_delta"]
    assert agent["available_credits"] == expected_available
