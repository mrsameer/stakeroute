"""The consume loop and subject handlers (contracts/events.md).

Depends only on the ``SignalTransport`` protocol, so it runs identically
against the in-process test driver and the real JetStream driver — Phase 7
swaps the transport, not this module (D-003). The ordering below is the
whole idempotency argument (Constitution Principle III): insert the event
inside the same transaction as the handler's effect, commit, and only then
acknowledge.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from stakeroute.config import STAKE_MAX, STAKE_MIN
from stakeroute.core.types import clamp_probability
from stakeroute.storage.repository import Repository
from stakeroute.worker.settlement_runner import settle_hypothesis

SIGNALS_RAW = "signals.raw"
FORECASTS_CREATED = "forecasts.created"
OUTCOMES_RESOLVED = "outcomes.resolved"
HYPOTHESES_UPDATED = "hypotheses.updated"

Handler = Callable[[dict, Repository, str], None]


async def consume_once(
    transport,
    subject: str,
    durable_name: str,
    repo: Repository,
    tenant_id: str,
    handler: Handler,
) -> int:
    """Drain every currently pending message on ``subject``.

    Returns the number of messages that produced a new effect (a duplicate
    delivery inserts no event row, applies no handler, and still gets
    acknowledged — that is what makes redelivery safe).
    """
    applied = 0
    async for payload, ack in transport.subscribe(subject, durable_name):
        # observed_at_ms is when the signal was originally emitted
        # (simulated time, possibly spread across a wide window);
        # ingested_at_ms is when THIS pipeline actually processed it —
        # real wall-clock time. Conflating the two would make throughput
        # measure the simulator's signal cadence instead of the pipeline's
        # own processing rate (Performance Goals, plan.md).
        is_new = repo.insert_event(
            event_id=payload["event_id"],
            tenant_id=payload.get("tenant_id", tenant_id),
            source=subject,
            source_event_id=payload.get("payload", {}).get(
                "source_event_id", payload["event_id"]
            ),
            observed_at_ms=payload.get("emitted_at_ms", 0),
            ingested_at_ms=int(time.time() * 1000),
            provenance=payload.get("payload", {}).get("provenance", {}),
            payload=payload.get("payload", {}),
        )
        if is_new:
            handler(payload, repo, tenant_id)
            applied += 1
        repo.commit()
        await ack()
    return applied


def handle_signal(payload: dict, repo: Repository, tenant_id: str) -> None:
    """``signals.raw``: the event insert above is the entire effect.

    Signals carry no direct economic consequence; they exist as durable,
    inspectable provenance for the forecasts that cite them.
    """


def handle_forecast_created(payload: dict, repo: Repository, tenant_id: str) -> None:
    """``forecasts.created``: validate, lock stake, upsert the live forecast.

    One live forecast per ``(hypothesis_id, agent_id)`` (FR-044): a
    resubmission replaces the prior row and the stake difference is
    reconciled against the agent's balance rather than accumulating votes.
    """
    body = payload["payload"]
    hypothesis_id = body["hypothesis_id"]
    agent_id = body["agent_id"]
    stake = int(body["stake"])
    probability = clamp_probability(float(body["probability"]))

    reject_at_ms = payload.get("emitted_at_ms", 0)

    agent = repo.get_agent(agent_id)
    if agent is None:
        repo.insert_rejected_forecast(
            tenant_id,
            hypothesis_id,
            agent_id,
            stake,
            probability,
            "unknown agent",
            reject_at_ms,
        )
        return

    if not (STAKE_MIN <= stake <= STAKE_MAX):
        repo.insert_rejected_forecast(
            tenant_id,
            hypothesis_id,
            agent_id,
            stake,
            probability,
            f"stake {stake} outside configured limits [{STAKE_MIN}, {STAKE_MAX}]",
            reject_at_ms,
        )
        return

    # Validate against the *net* delta: a resubmission on the same
    # hypothesis first frees its old stake, so the true ceiling on the new
    # stake is available_credits + whatever this agent already has locked
    # here — not the raw available balance alone (FR-044).
    existing_forecast = repo.get_live_forecast(hypothesis_id, agent_id)
    already_locked_here = existing_forecast["stake"] if existing_forecast else 0
    effective_available = agent["available_credits"] + already_locked_here
    if stake > effective_available:
        repo.insert_rejected_forecast(
            tenant_id,
            hypothesis_id,
            agent_id,
            stake,
            probability,
            f"stake {stake} exceeds effective available credits {effective_available}",
            reject_at_ms,
        )
        return

    evidence_cluster_id = body["evidence_cluster_id"]
    repo.ensure_evidence_cluster(evidence_cluster_id, tenant_id, evidence_cluster_id)

    forecast_id = f"forecast-{hypothesis_id}-{agent_id}"
    previous = repo.upsert_forecast(
        forecast_id=forecast_id,
        tenant_id=tenant_id,
        hypothesis_id=hypothesis_id,
        agent_id=agent_id,
        probability=probability,
        stake=stake,
        evidence_cluster_id=evidence_cluster_id,
        evidence_refs=body.get("evidence_refs", []),
        source_event_id=payload["event_id"],
        created_at_ms=payload.get("emitted_at_ms", 0),
        expires_at_ms=body.get("expires_at_ms", 0),
    )
    if previous is not None:
        _, previous_stake = previous.split(":")
        repo.adjust_agent_credits(
            agent_id,
            available_delta=int(previous_stake),
            staked_delta=-int(previous_stake),
        )
    repo.adjust_agent_credits(agent_id, available_delta=-stake, staked_delta=stake)


def handle_outcome_resolved(payload: dict, repo: Repository, tenant_id: str) -> None:
    """``outcomes.resolved``: the strictest handler in the system.

    Settlement runs inside one transaction (settle_hypothesis): insert
    settlements with ``UNIQUE(forecast_id)``, apply integer credit deltas,
    update reputations, release stakes, mark the hypothesis resolved. A
    redelivered resolution conflicts on the ``outcomes`` and
    ``settlements`` uniqueness constraints and applies nothing.
    """
    body = payload["payload"]
    hypothesis_id = body["hypothesis_id"]
    outcome = int(body["outcome"])
    resolved_by = body.get("resolved_by", "operator")
    resolved_at_ms = payload.get("emitted_at_ms", 0)
    settle_hypothesis(
        repo, tenant_id, hypothesis_id, outcome, resolved_by, resolved_at_ms
    )
