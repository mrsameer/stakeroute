"""FastAPI dashboard: HTTP API, WebSocket, and static UI.

Contract: contracts/http-api.md. No authentication — explicitly out of
scope per the spec. This process also drives scenario ingestion directly
through the in-process transport (Phase 3–6); Phase 7 adds a standalone
``worker`` process reading the same subjects from JetStream instead.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Iterable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from stakeroute.config import ATTENTION_BUDGET, DB_PATH, DEFAULT_TENANT_ID, EPOCH_GRANT
from stakeroute.core.types import compute_event_id
from stakeroute.metrics import (
    events_per_second,
    false_escalation_rate,
    mean_brier_score,
    precision_at_k,
    ranking_pass_lag_ms,
    time_to_attention_ms,
)
from stakeroute.simulator.agents import AgentProfile
from stakeroute.simulator.scenarios import ForecastSpec, ScenarioWorld, generate_world
from stakeroute.simulator.stress import inject_correlated, inject_sybils
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
from stakeroute.worker.pipeline import (
    STRATEGIES,
    publish_hypothesis_updates,
    run_ranking_pass,
)

app = FastAPI(title="StakeRoute")

_repo: Repository | None = None
_transport = MemoryTransport()
# Ground truth is a simulator-known fact, not part of the persisted domain
# model — the real system does not know it before resolution. Exposed here
# only so the attack demo (User Story 2) can be narrated against reality
# before a hypothesis actually resolves. Reset on every run_normal.
_ground_truth: dict[str, int] = {}
_run_id: str | None = None


def now_ms() -> int:
    return int(time.time() * 1000)


def get_repo() -> Repository:
    global _repo
    if _repo is None:
        _repo = Repository(DB_PATH)
        _repo.ensure_tenant(DEFAULT_TENANT_ID, "AcmePay", now_ms())
        _repo.commit()
    return _repo


async def _ingest_signals(
    repo: Repository, transport: MemoryTransport, tenant_id: str, world: ScenarioWorld
) -> None:
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


def _ingest_agents(
    repo: Repository, agents: Iterable[AgentProfile], tenant_id: str, now: int
) -> None:
    for agent in agents:
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


async def _ingest_forecasts(
    repo: Repository,
    transport: MemoryTransport,
    tenant_id: str,
    forecasts: Iterable[ForecastSpec],
    now: int,
) -> None:
    """Publish forecasts to ``forecasts.created`` and consume them.

    Mirrors the real subject flow (contracts/events.md) end to end over the
    in-process transport — the same ``consume_once`` and handler Phase 7
    points at a live broker instead.
    """
    for forecast in forecasts:
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
    global _run_id
    repo = get_repo()
    tenant_id = DEFAULT_TENANT_ID
    now = now_ms()
    repo.reset_tenant(tenant_id)
    _ground_truth.clear()
    _run_id = f"run-{request.seed}-{now}"

    world = generate_world(seed=request.seed, reference_ms=now)
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
        _ground_truth[hypothesis.id] = hypothesis.ground_truth
    repo.commit()

    _ingest_agents(repo, world.agents, tenant_id, now)
    await _ingest_signals(repo, _transport, tenant_id, world)
    await _ingest_forecasts(repo, _transport, tenant_id, world.forecasts, now)

    results = run_ranking_pass(repo, tenant_id, ATTENTION_BUDGET, now)
    await publish_hypothesis_updates(_transport, tenant_id, results, now)
    stakeroute_result = results["stakeroute"]
    routed = sum(1 for d in stakeroute_result.allocation.decisions if d.routed)
    return {
        "seed": request.seed,
        "routed": routed,
        "withheld_count": stakeroute_result.allocation.withheld_count,
    }


class InjectSybilsRequest(BaseModel):
    count: int = 50
    target: str = "h-database-saturation"
    seed: int = 1000


@app.post("/api/scenario/inject_sybils")
async def inject_sybils_endpoint(request: InjectSybilsRequest) -> dict:
    """Flood ``target`` with new, unattested, floor-reputation agents (SC-002)."""
    repo = get_repo()
    tenant_id = DEFAULT_TENANT_ID
    now = now_ms()
    rng = random.Random(request.seed)
    agents, forecasts = inject_sybils(rng, request.count, request.target)
    _ingest_agents(repo, agents, tenant_id, now)
    await _ingest_forecasts(repo, _transport, tenant_id, forecasts, now)
    results = run_ranking_pass(repo, tenant_id, ATTENTION_BUDGET, now)
    await publish_hypothesis_updates(_transport, tenant_id, results, now)
    return {"injected": request.count, "target": request.target}


class InjectCorrelatedRequest(BaseModel):
    count: int = 20
    cluster: str = "database-observability"
    target: str = "h-database-saturation"
    seed: int = 2000


@app.post("/api/scenario/inject_correlated")
async def inject_correlated_endpoint(request: InjectCorrelatedRequest) -> dict:
    """Flood ``target`` with agents all citing the same evidence cluster (SC-003)."""
    repo = get_repo()
    tenant_id = DEFAULT_TENANT_ID
    now = now_ms()
    rng = random.Random(request.seed)
    agents, forecasts = inject_correlated(
        rng, request.count, request.cluster, request.target
    )
    _ingest_agents(repo, agents, tenant_id, now)
    await _ingest_forecasts(repo, _transport, tenant_id, forecasts, now)
    results = run_ranking_pass(repo, tenant_id, ATTENTION_BUDGET, now)
    await publish_hypothesis_updates(_transport, tenant_id, results, now)
    return {
        "injected": request.count,
        "cluster": request.cluster,
        "target": request.target,
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


@app.get("/api/comparison")
def comparison() -> dict:
    """Side-by-side strategy rankings over the identical event stream
    (FR-023, FR-032, SC-002)."""
    repo = get_repo()
    tenant_id = DEFAULT_TENANT_ID
    strategies = {}
    for strategy in STRATEGIES:
        decisions = sorted(
            repo.latest_decisions(tenant_id, strategy), key=lambda r: r["rank"]
        )
        strategies[strategy] = [
            {
                "rank": row["rank"],
                "hypothesis_id": row["hypothesis_id"],
                "probability": row["aggregated_probability"],
            }
            for row in decisions
        ]
    return {"strategies": strategies, "ground_truth": dict(_ground_truth)}


class ResolveRequest(BaseModel):
    hypothesis_id: str
    outcome: int


@app.post("/api/scenario/resolve")
async def resolve(request: ResolveRequest) -> dict:
    """Publish to ``outcomes.resolved``, triggering settlement (FR-024)."""
    repo = get_repo()
    tenant_id = DEFAULT_TENANT_ID
    now = now_ms()
    event_id = compute_event_id(tenant_id, "outcomes", request.hypothesis_id, now)
    await _transport.publish(
        OUTCOMES_RESOLVED,
        {
            "tenant_id": tenant_id,
            "event_id": event_id,
            "emitted_at_ms": now,
            "payload": {
                "hypothesis_id": request.hypothesis_id,
                "outcome": request.outcome,
                "resolved_by": "operator",
                "resolved_at_ms": now,
            },
        },
    )
    await consume_once(
        _transport,
        OUTCOMES_RESOLVED,
        "dashboard",
        repo,
        tenant_id,
        handle_outcome_resolved,
    )
    return {"hypothesis_id": request.hypothesis_id, "outcome": request.outcome}


@app.get("/api/agents")
def list_agents() -> dict:
    """Reputation, credits, and forecast/settlement state per agent."""
    repo = get_repo()
    tenant_id = DEFAULT_TENANT_ID
    agents = []
    for row in repo.list_agents(tenant_id):
        agents.append(
            {
                "id": row["id"],
                "display_name": row["display_name"],
                "reputation": row["reputation"],
                "available_credits": row["available_credits"],
                "staked_credits": row["staked_credits"],
                "attested": bool(row["attested"]),
                "last_forecast": repo.get_last_forecast_probability(row["id"]),
                "last_settlement": repo.get_last_settlement_delta(row["id"]),
            }
        )
    return {"agents": agents}


@app.get("/api/metrics")
def metrics() -> dict:
    """Five metrics plus ranking-pass lag, all from recorded run data.

    Every field is nullable; ``null`` means not yet measured. Fabricating
    a value here would be a constitutional violation (FR-034) — the
    ``measured_over_events``/``measured_over`` counts alongside each value
    are what make "measured" checkable rather than asserted.
    """
    repo = get_repo()
    tenant_id = DEFAULT_TENANT_ID

    precision, precision_n = precision_at_k(repo, tenant_id)
    false_escalation, false_escalation_n = false_escalation_rate(repo, tenant_id)
    time_to_attention, tta_n = time_to_attention_ms(repo, tenant_id)
    brier, brier_n = mean_brier_score(repo, tenant_id)
    throughput, throughput_n = events_per_second(repo, tenant_id)
    lag, _lag_n = ranking_pass_lag_ms(repo, tenant_id)

    return {
        "precision_at_k": precision,
        "false_escalation_rate": false_escalation,
        "time_to_attention_ms": time_to_attention,
        "mean_brier_score": brier,
        "events_per_second": throughput,
        "ranking_pass_lag_ms": lag,
        "measured_over_events": repo.count_events(tenant_id),
        "measured_over": {
            "precision_at_k": precision_n,
            "false_escalation_rate": false_escalation_n,
            "time_to_attention_ms": tta_n,
            "mean_brier_score": brier_n,
            "events_per_second": throughput_n,
        },
        "run_id": _run_id,
    }
