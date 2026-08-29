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
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from stakeroute.analysis.cost_of_attack import NoRankingRecorded, build_report
from stakeroute.config import (
    ATTENTION_BUDGET,
    DB_PATH,
    DEFAULT_TENANT_ID,
    DUPLICATE_JACCARD_THRESHOLD,
    DUPLICATE_WINDOW_MS,
    EPOCH_GRANT,
    MIN_RESOLVED_FOR_CALIBRATION,
    MODEL_CEILING_CALLS_PER_HOUR,
    REAL_TENANT_ID,
    STAKE_MAX,
    STAKEROUTE_MODEL,
)
from stakeroute.core.duplicates import ProposalFingerprint, find_duplicate
from stakeroute.core.types import compute_event_id
from stakeroute.metrics import (
    events_per_second,
    false_escalation_rate,
    mean_brier_score,
    precision_at_k,
    ranking_pass_lag_ms,
    time_to_attention_ms,
)
from stakeroute.model.protocol import (
    CAPABILITY_HYPOTHESIS_PROPOSAL,
    CAPABILITY_PROSE_EXPLANATION,
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
_ws_clients: set[WebSocket] = set()


async def _broadcast_queue_updated() -> None:
    """Push a lightweight "something changed" notice to every connected
    WS client. The client repaints from GET /api/queue itself (contracts/
    http-api.md) — the socket carries no state of its own, so a client
    that reconnects after the worker was killed loses nothing."""
    dead: set[WebSocket] = set()
    for client in _ws_clients:
        try:
            await client.send_json({"type": "queue_updated"})
        except Exception:
            dead.add(client)
    _ws_clients.difference_update(dead)


def now_ms() -> int:
    return int(time.time() * 1000)


def get_repo() -> Repository:
    global _repo
    if _repo is None:
        _repo = Repository(DB_PATH)
        _repo.ensure_tenant(DEFAULT_TENANT_ID, "AcmePay", now_ms())
        _repo.ensure_tenant(REAL_TENANT_ID, "Host Operations", now_ms())
        _repo.commit()
    return _repo


def _resolve_tenant(request: Request, default: str) -> str:
    """Every data endpoint takes an explicit, single ``tenant`` (D-018,
    SC-117). A request naming more than one is refused outright —
    cross-tenant aggregation must require new code, not a missing filter.
    """
    values = request.query_params.getlist("tenant")
    if len(values) > 1:
        raise HTTPException(
            status_code=400,
            detail=f"a request may name exactly one tenant, got {values}",
        )
    tenant = values[0] if values else default
    if "," in tenant:
        raise HTTPException(
            status_code=400,
            detail=f"a request may name exactly one tenant, got {tenant!r}",
        )
    return tenant


def _mode_for_tenant(tenant_id: str) -> str:
    return "real" if tenant_id == REAL_TENANT_ID else "sim"


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
    await _broadcast_queue_updated()
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
    await _broadcast_queue_updated()
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
    await _broadcast_queue_updated()
    return {
        "injected": request.count,
        "cluster": request.cluster,
        "target": request.target,
    }


@app.get("/api/mode")
def get_mode(request: Request) -> dict:
    """FR-139, FR-141: what an operator is looking at, in one interaction —
    real, simulated or replay, never inferred."""
    repo = get_repo()
    tenant_id = _resolve_tenant(request, DEFAULT_TENANT_ID)
    mode = _mode_for_tenant(tenant_id)

    span = repo.event_ingestion_span_ms(tenant_id)
    since_ms = span[1] if span else None

    if STAKEROUTE_MODEL == "none":
        model_block = {
            "state": "unconfigured",
            "detail": "no model configured",
            "unavailable_capabilities": [
                CAPABILITY_HYPOTHESIS_PROPOSAL,
                CAPABILITY_PROSE_EXPLANATION,
            ],
            "usage": {"calls_this_hour": 0, "ceiling": 0},
        }
    else:
        recent = repo.list_model_interactions(tenant_id, since_ms=now_ms() - 3_600_000)
        calls_this_hour = len(recent)
        ceiling = MODEL_CEILING_CALLS_PER_HOUR
        if calls_this_hour >= ceiling:
            state, detail = (
                "ceiling_reached",
                f"{calls_this_hour}/{ceiling} calls this hour",
            )
            unavailable = [CAPABILITY_HYPOTHESIS_PROPOSAL, CAPABILITY_PROSE_EXPLANATION]
        else:
            last_three = sorted(recent, key=lambda r: r["requested_at_ms"])[-3:]
            if len(last_three) == 3 and all(not r["accepted"] for r in last_three):
                state, detail = "degraded", "3 consecutive rejections"
                unavailable = [CAPABILITY_HYPOTHESIS_PROPOSAL]
            else:
                state, detail, unavailable = "ok", "", []
        model_block = {
            "state": state,
            "detail": detail,
            "unavailable_capabilities": unavailable,
            "usage": {"calls_this_hour": calls_this_hour, "ceiling": ceiling},
        }

    sources = []
    for row in repo.list_observation_sources(tenant_id):
        source = {
            "id": row["id"],
            "display_name": row["display_name"],
            "state": row["state"],
            "last_seen_ms": row["last_seen_ms"],
            "set_aside_count": row["set_aside_count"],
        }
        if row["state"] == "absent":
            source["absent_reason"] = row["absent_reason"]
        sources.append(source)

    return {
        "mode": mode,
        "tenant": tenant_id,
        "since_ms": since_ms,
        "model": model_block,
        "sources": sources,
    }


def _estimates_block(repo: Repository, hypothesis_id: str) -> dict:
    """FR-108, FR-109: every estimate carries its basis and whether an
    operator has confirmed it — real-mode hypotheses only, since simulated
    ones carry no ``attribute_estimates`` rows at all."""
    block = {}
    for row in repo.current_attribute_estimates(hypothesis_id):
        block[row["attribute"]] = {
            "value": row["value"],
            "basis": row["basis"],
            "confirmed": bool(row["confirmed_by_operator"]),
        }
    return block


def _flagged_duplicates(
    repo: Repository, tenant_id: str, hypothesis_ids: list[str]
) -> list[dict]:
    """Live re-scan for probable (Jaccard-flagged, not exact-merged)
    duplicates among the currently routed hypotheses (D-023). This is
    recomputed on read, not stored — a merged duplicate never reaches here
    at all, since ``real/proposal.py`` never promotes it to a hypothesis
    (FR-110)."""
    fingerprints = []
    for hypothesis_id in hypothesis_ids:
        hypothesis = repo.get_hypothesis(hypothesis_id)
        proposal_id = hypothesis["proposal_id"] if hypothesis else None
        if not proposal_id:
            continue
        proposal = repo.get_proposal(proposal_id)
        if proposal is None:
            continue
        fingerprints.append(
            ProposalFingerprint(
                id=hypothesis_id,
                condition_name=proposal["condition_name"],
                condition_params=(
                    json.loads(proposal["condition_params"])
                    if proposal["condition_params"]
                    else None
                ),
                cited_observation_ids=frozenset(
                    json.loads(proposal["cited_observation_ids"])
                ),
                created_at_ms=proposal["created_at_ms"],
            )
        )

    flagged = []
    for fp in fingerprints:
        others = tuple(o for o in fingerprints if o.id != fp.id)
        match = find_duplicate(
            fp, others, DUPLICATE_JACCARD_THRESHOLD, DUPLICATE_WINDOW_MS
        )
        if match is not None:
            flagged.append(
                {
                    "hypothesis_id": fp.id,
                    "probable_duplicate_of": match.other_id,
                    "basis": match.basis,
                }
            )
    return flagged


@app.get("/api/queue")
def get_queue(request: Request) -> dict:
    """The operator's screen (FR-030, FR-020, FR-022, FR-108, FR-110)."""
    repo = get_repo()
    tenant_id = _resolve_tenant(request, DEFAULT_TENANT_ID)
    mode = _mode_for_tenant(tenant_id)
    decisions = repo.latest_decisions(tenant_id, "stakeroute")

    routed_entries = []
    for row in sorted((d for d in decisions if d["routed"]), key=lambda r: r["rank"]):
        hypothesis = repo.get_hypothesis(row["hypothesis_id"])
        contributions = json.loads(row["contributions"])
        cluster_ids = {c["evidence_cluster_id"] for c in contributions}
        discounted = sum(1 for c in contributions if c["cluster_size"] > 1)
        entry = {
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
            "age_ms": now_ms() - hypothesis["created_at_ms"] if hypothesis else None,
        }
        if mode == "real" and hypothesis is not None:
            proposal = (
                repo.get_proposal(hypothesis["proposal_id"])
                if hypothesis["proposal_id"]
                else None
            )
            entry["estimates"] = _estimates_block(repo, row["hypothesis_id"])
            entry["cited_observation_count"] = (
                len(json.loads(proposal["cited_observation_ids"])) if proposal else 0
            )
            entry["condition"] = (
                {
                    "name": hypothesis["condition_name"],
                    "params": json.loads(hypothesis["condition_params"])
                    if hypothesis["condition_params"]
                    else None,
                }
                if hypothesis["condition_name"]
                else None
            )
            entry["duplicate_of"] = None
        routed_entries.append(entry)

    withheld_count = sum(1 for d in decisions if not d["routed"])
    result = {
        "mode": mode,
        "attention_budget": ATTENTION_BUDGET,
        "slots_used": len(routed_entries),
        "withheld_count": withheld_count,
        "routed": routed_entries,
    }
    if mode == "real":
        result["flagged_duplicates"] = _flagged_duplicates(
            repo, tenant_id, [e["hypothesis_id"] for e in routed_entries]
        )
    return result


class ConfirmEstimateRequest(BaseModel):
    attribute: str
    value: float
    note: str = ""


@app.post("/api/estimates/{hypothesis_id}/confirm")
def confirm_estimate(hypothesis_id: str, request: ConfirmEstimateRequest) -> dict:
    """FR-109: an operator corrects or confirms an estimated attribute.

    Inserts a **new** ``attribute_estimates`` row and supersedes the prior
    one — estimates are never updated in place, so the correction history
    stays auditable.
    """
    repo = get_repo()
    hypothesis = repo.get_hypothesis(hypothesis_id)
    if hypothesis is None:
        raise HTTPException(status_code=404, detail="hypothesis not found")

    superseded = [
        row
        for row in repo.current_attribute_estimates(hypothesis_id)
        if row["attribute"] == request.attribute
    ]
    confirmed_at_ms = now_ms()
    basis = (
        f"operator-confirmed: {request.note}" if request.note else "operator-confirmed"
    )
    new_id = repo.insert_attribute_estimate(
        tenant_id=hypothesis["tenant_id"],
        hypothesis_id=hypothesis_id,
        attribute=request.attribute,
        value=request.value,
        basis=basis,
        estimator="operator",
        confirmed_by_operator=True,
        confirmed_at_ms=confirmed_at_ms,
        created_at_ms=confirmed_at_ms,
    )
    for row in superseded:
        repo.supersede_attribute_estimate(row["id"], new_id)
    repo.commit()

    return {
        "hypothesis_id": hypothesis_id,
        "attribute": request.attribute,
        "value": request.value,
        "basis": basis,
        "confirmed": True,
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
    await _broadcast_queue_updated()
    return {"hypothesis_id": request.hypothesis_id, "outcome": request.outcome}


@app.get("/api/agents")
def list_agents(request: Request) -> dict:
    """Reputation, credits, and forecast/settlement state per agent."""
    repo = get_repo()
    tenant_id = _resolve_tenant(request, DEFAULT_TENANT_ID)
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
def metrics(request: Request) -> dict:
    """Five metrics plus ranking-pass lag, all from recorded run data.

    Every field is nullable; ``null`` means not yet measured. Fabricating
    a value here would be a constitutional violation (FR-034) — the
    ``measured_over_events``/``measured_over`` counts alongside each value
    are what make "measured" checkable rather than asserted. Real mode adds
    a second reason for ``null``: too few resolved outcomes (FR-118,
    FR-142, D-022), and a required ``provenance`` on the whole response
    (FR-144).
    """
    repo = get_repo()
    tenant_id = _resolve_tenant(request, DEFAULT_TENANT_ID)
    mode = _mode_for_tenant(tenant_id)

    precision, precision_n = precision_at_k(repo, tenant_id)
    false_escalation, false_escalation_n = false_escalation_rate(repo, tenant_id)
    time_to_attention, tta_n = time_to_attention_ms(repo, tenant_id)
    brier, brier_n = mean_brier_score(repo, tenant_id)
    throughput, throughput_n = events_per_second(repo, tenant_id)
    lag, _lag_n = ranking_pass_lag_ms(repo, tenant_id)

    resolved_count = len(
        [h for h in repo.list_hypotheses(tenant_id) if repo.get_outcome(h["id"])]
    )
    result = {
        "tenant": tenant_id,
        "mode": mode,
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
        "provenance": "measured-real" if mode == "real" else "measured-simulated",
    }
    if mode == "real":
        insufficient = resolved_count < MIN_RESOLVED_FOR_CALIBRATION
        result["measured_over_outcomes"] = resolved_count
        result["minimum_for_calibration"] = MIN_RESOLVED_FOR_CALIBRATION
        result["insufficient"] = insufficient
        result["insufficient_reason"] = (
            f"{resolved_count} resolved outcomes; "
            f"{MIN_RESOLVED_FOR_CALIBRATION} required"
            if insufficient
            else None
        )
        if insufficient:
            result["precision_at_k"] = None
            result["false_escalation_rate"] = None
            result["mean_brier_score"] = None
    return result


@app.get("/api/cost_of_attack")
def cost_of_attack(
    target: str | None = None,
    stake_per_identity: int = STAKE_MAX,
    shared_evidence_cluster: bool = False,
) -> dict:
    """What it would cost to buy rank 1, under each strategy (FR-023).

    Derived from the recorded ranking pass, not simulated: every input is
    a number the last pass already wrote to the ledger. This endpoint
    reads; it never ranks, ingests, or settles.
    """
    repo = get_repo()
    try:
        return build_report(
            repo,
            DEFAULT_TENANT_ID,
            target_hypothesis_id=target,
            stake_per_identity=stake_per_identity,
            shared_evidence_cluster=shared_evidence_cluster,
        )
    except NoRankingRecorded as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.websocket("/api/live")
async def live(websocket: WebSocket) -> None:
    """Server-push updates. Payloads mirror ``hypotheses.updated``.

    The socket itself carries no state — a client that reconnects after
    the worker was killed loses nothing, because it repaints from
    GET /api/queue on connect and on every subsequent push (contracts/
    http-api.md; T080 implements the client side of this).
    """
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        while True:
            # We never expect the client to send anything; this just
            # blocks until the socket closes, so we notice disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)


_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")
