"""The consume loop and subject handlers (contracts/events.md).

Depends only on the ``SignalTransport`` protocol, so it runs identically
against the in-process test driver and the real JetStream driver — Phase 7
swaps the transport, not this module (D-003). The ordering below is the
whole idempotency argument (Constitution Principle III): insert the event
inside the same transaction as the handler's effect, commit, and only then
acknowledge.
"""

from __future__ import annotations

from collections.abc import Callable

from stakeroute.config import STAKE_MAX, STAKE_MIN
from stakeroute.core.types import clamp_probability
from stakeroute.storage.repository import Repository

SIGNALS_RAW = "signals.raw"
FORECASTS_CREATED = "forecasts.created"
OUTCOMES_RESOLVED = "outcomes.resolved"

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
        is_new = repo.insert_event(
            event_id=payload["event_id"],
            tenant_id=payload.get("tenant_id", tenant_id),
            source=subject,
            source_event_id=payload.get("payload", {}).get(
                "source_event_id", payload["event_id"]
            ),
            observed_at_ms=payload.get("emitted_at_ms", 0),
            ingested_at_ms=payload.get("emitted_at_ms", 0),
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

    agent = repo.get_agent(agent_id)
    if agent is None:
        return  # rejected: unknown/unattested agent

    if not (STAKE_MIN <= stake <= STAKE_MAX):
        return  # rejected: stake outside configured limits (FR-008)

    previous_available = agent["available_credits"]
    if stake > previous_available:
        return  # rejected: insufficient available credits (FR-008)

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
