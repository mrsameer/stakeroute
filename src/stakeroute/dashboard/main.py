"""FastAPI dashboard: HTTP API, WebSocket, and static UI.

Contract: contracts/http-api.md. No authentication — explicitly out of
scope per the spec. This process also drives scenario ingestion directly
through the in-process transport (Phase 3–6); Phase 7 adds a standalone
``worker`` process reading the same subjects from JetStream instead.
"""

from __future__ import annotations

import json
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from stakeroute.config import ATTENTION_BUDGET, DB_PATH, DEFAULT_TENANT_ID, EPOCH_GRANT
from stakeroute.core.types import compute_event_id
from stakeroute.simulator.scenarios import ScenarioWorld, generate_world
from stakeroute.storage.repository import Repository
from stakeroute.transport.memory import MemoryTransport
from stakeroute.worker.main import (
    FORECASTS_CREATED,
    SIGNALS_RAW,
    consume_once,
    handle_forecast_created,
    handle_signal,
)
from stakeroute.worker.pipeline import run_ranking_pass

app = FastAPI(title="StakeRoute")

_repo: Repository | None = None
_transport = MemoryTransport()


def now_ms() -> int:
    return int(time.time() * 1000)


def get_repo() -> Repository:
    global _repo
    if _repo is None:
        _repo = Repository(DB_PATH)
        _repo.ensure_tenant(DEFAULT_TENANT_ID, "AcmePay", now_ms())
        _repo.commit()
    return _repo


async def _ingest_world(
    repo: Repository,
    transport: MemoryTransport,
    tenant_id: str,
    world: ScenarioWorld,
    now: int,
) -> None:
    """Publish a scenario world's signals and forecasts, then consume them.

    Mirrors the real subject flow (contracts/events.md) end to end, just
    over the in-process transport rather than JetStream — the same
    ``consume_once`` and handlers Phase 7 points at a live broker.
    """
    for signal in world.signals:
        event_id = compute_event_id(
            tenant_id, signal.source, signal.source_event_id, signal.observed_at_ms
        )
        await transport.publish(
            SIGNALS_RAW,
            {
                "tenant_id": tenant_id,
                "event_id": event_id,
                "emitted_at_ms": signal.observed_at_ms,
                "payload": {
                    "source": signal.source,
                    "source_event_id": signal.source_event_id,
                    "provenance": {"system": "simulator", "collector": "sim"},
                    "payload": signal.payload,
                },
            },
        )
    await consume_once(
        transport, SIGNALS_RAW, "dashboard", repo, tenant_id, handle_signal
    )

    for hypothesis in world.hypotheses:
        repo.upsert_hypothesis(
            hypothesis_id=hypothesis.id,
            tenant_id=tenant_id,
            statement=hypothesis.statement,
            prior_probability=hypothesis.prior_probability,
            impact_minor_units=hypothesis.impact_minor_units,
            urgency=hypothesis.urgency,
            review_cost=hypothesis.review_cost,
            deadline_ms=hypothesis.deadline_ms,
            status="open",
            created_at_ms=hypothesis.created_at_ms,
        )
    repo.commit()

    for agent in world.agents:
        repo.upsert_agent(
            agent_id=agent.agent_id,
            tenant_id=tenant_id,
            display_name=agent.display_name,
            reputation=agent.starting_reputation,
            available_credits=EPOCH_GRANT,
            staked_credits=0,
            attested=agent.attested,
            created_at_ms=now,
        )
    repo.commit()

    for forecast in world.forecasts:
        event_id = compute_event_id(
            tenant_id, "forecasts", forecast.source_event_id, now
        )
        await transport.publish(
            FORECASTS_CREATED,
            {
                "tenant_id": tenant_id,
                "event_id": event_id,
                "emitted_at_ms": now,
                "payload": {
                    "hypothesis_id": forecast.hypothesis_id,
                    "agent_id": forecast.agent_id,
                    "probability": forecast.probability,
                    "stake": forecast.stake,
                    "evidence_cluster_id": forecast.evidence_cluster_id,
                    "evidence_refs": [],
                    "source_event_id": forecast.source_event_id,
                    "expires_at_ms": now + 3_600_000,
                },
            },
        )
    await consume_once(
        transport,
        FORECASTS_CREATED,
        "dashboard",
        repo,
        tenant_id,
        handle_forecast_created,
    )


class RunNormalRequest(BaseModel):
    seed: int = 42


@app.post("/api/scenario/run_normal")
async def run_normal(request: RunNormalRequest) -> dict:
    """Reset the tenant and run the baseline scenario (quickstart V1)."""
    repo = get_repo()
    tenant_id = DEFAULT_TENANT_ID
    now = now_ms()
    repo.reset_tenant(tenant_id)
    world = generate_world(seed=request.seed)
    await _ingest_world(repo, _transport, tenant_id, world, now)
    result = run_ranking_pass(repo, tenant_id, ATTENTION_BUDGET, now)
    routed = sum(1 for d in result.allocation.decisions if d.routed)
    return {
        "seed": request.seed,
        "routed": routed,
        "withheld_count": result.allocation.withheld_count,
    }


@app.get("/api/queue")
def get_queue() -> dict:
    """The operator's screen (FR-030, FR-020, FR-022)."""
    repo = get_repo()
    tenant_id = DEFAULT_TENANT_ID
    decisions = repo.latest_decisions(tenant_id, "stakeroute")

    routed_entries = []
    for row in sorted((d for d in decisions if d["routed"]), key=lambda r: r["rank"]):
        hypothesis = repo.get_hypothesis(row["hypothesis_id"])
        contributions = json.loads(row["contributions"])
        cluster_ids = {c["evidence_cluster_id"] for c in contributions}
        discounted = sum(1 for c in contributions if c["cluster_size"] > 1)
        routed_entries.append(
            {
                "hypothesis_id": row["hypothesis_id"],
                "statement": hypothesis["statement"]
                if hypothesis
                else row["hypothesis_id"],
                "probability": row["aggregated_probability"],
                "impact_minor_units": hypothesis["impact_minor_units"]
                if hypothesis
                else None,
                "priority": row["priority"],
                "rank": row["rank"],
                "independent_evidence_groups": len(cluster_ids),
                "discounted_report_count": discounted,
                "reason": row["reason"],
                "age_ms": now_ms() - hypothesis["created_at_ms"]
                if hypothesis
                else None,
            }
        )

    withheld_count = sum(1 for d in decisions if not d["routed"])
    return {
        "attention_budget": ATTENTION_BUDGET,
        "slots_used": len(routed_entries),
        "withheld_count": withheld_count,
        "routed": routed_entries,
    }


@app.get("/api/hypotheses/{hypothesis_id}/explain")
def explain(hypothesis_id: str) -> dict:
    """The Principle II drill-down (FR-017). Every /api/queue field must be
    reconstructible from this response."""
    repo = get_repo()
    hypothesis = repo.get_hypothesis(hypothesis_id)
    if hypothesis is None:
        raise HTTPException(status_code=404, detail="hypothesis not found")

    decisions = repo.latest_decisions(DEFAULT_TENANT_ID, "stakeroute")
    row = next((d for d in decisions if d["hypothesis_id"] == hypothesis_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="no ranking decision recorded yet")

    return {
        "hypothesis_id": hypothesis_id,
        "prior_probability": hypothesis["prior_probability"],
        "aggregated_probability": row["aggregated_probability"],
        "contributions": json.loads(row["contributions"]),
    }
