"""SC-005: a consumer that dies before acknowledging is redelivered the
same message, and completing it applies no second economic effect.

Unlike test_idempotency.py (which redelivers a fresh publish of the same
envelope — an ACK lost in transit), this test drives the transport's
lower-level primitives directly to simulate the literal failure mode:
receive, apply the effect, commit — then the process dies *before* the
acknowledgement reaches the broker. The broker's own behaviour (redeliver
anything un-acked) is what ``MemoryTransport.simulate_crash`` reproduces.
"""

import asyncio

from stakeroute.core.types import compute_event_id
from stakeroute.storage.repository import Repository
from stakeroute.transport.memory import MemoryTransport
from stakeroute.worker.main import (
    FORECASTS_CREATED,
    OUTCOMES_RESOLVED,
    consume_once,
    handle_forecast_created,
    handle_outcome_resolved,
)

TENANT_ID = "acmepay"


def _repo(tmp_path) -> Repository:
    repo = Repository(str(tmp_path / "crash.db"))
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


async def _process_without_acking(transport, subject, repo, handler) -> None:
    """Receive one message, apply its effect, commit — then simulate the
    worker dying before the ack is sent (the ack coroutine is never
    awaited)."""
    async for payload, _ack in transport.subscribe(subject, "worker"):
        repo.insert_event(
            event_id=payload["event_id"],
            tenant_id=payload.get("tenant_id", TENANT_ID),
            source=subject,
            source_event_id=payload.get("payload", {}).get(
                "source_event_id", payload["event_id"]
            ),
            observed_at_ms=payload.get("emitted_at_ms", 0),
            ingested_at_ms=payload.get("emitted_at_ms", 0),
            provenance=payload.get("payload", {}).get("provenance", {}),
            payload=payload.get("payload", {}),
        )
        handler(payload, repo, TENANT_ID)
        repo.commit()
        break  # crash: return without calling _ack()


def test_forecast_survives_crash_before_ack_with_no_double_effect(tmp_path) -> None:
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
        # First delivery: apply the effect, commit, then "crash" before ack.
        await _process_without_acking(
            transport, FORECASTS_CREATED, repo, handle_forecast_created
        )
        assert transport.in_flight_count(FORECASTS_CREATED) == 1
        assert transport.pending_count(FORECASTS_CREATED) == 0

        # The broker notices the missing ack and redelivers.
        redelivered = transport.simulate_crash(FORECASTS_CREATED)
        assert redelivered == 1
        assert transport.pending_count(FORECASTS_CREATED) == 1

        # Recovery: the worker restarts and completes normally this time.
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
    # The stake was locked exactly once across both deliveries.
    assert agent["available_credits"] == 70
    assert agent["staked_credits"] == 30
    assert repo.count_events(TENANT_ID) == 1
    assert transport.in_flight_count(FORECASTS_CREATED) == 0
    assert transport.pending_count(FORECASTS_CREATED) == 0


def test_settlement_survives_crash_before_ack_with_no_double_effect(tmp_path) -> None:
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
        # Settlement applies, commits — then the worker "dies" before ack.
        await _process_without_acking(
            transport, OUTCOMES_RESOLVED, repo, handle_outcome_resolved
        )

        redelivered = transport.simulate_crash(OUTCOMES_RESOLVED)
        assert redelivered == 1

        # Recovery: redelivery completes, but the outcome and settlement
        # uniqueness constraints make it a no-op.
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
