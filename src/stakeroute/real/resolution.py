"""Automatic outcome determination (FR-133, FR-134, FR-135, FR-136, FR-138,
FR-148).

A hypothesis resolves because a condition of the machine was re-checked,
not because the program looked up an answer it had stored. ``outcomes``
(feature 001's settlement trigger) is written through only on a
hypothesis's *first* resolution, so ``worker/settlement_runner.py`` and
every feature-001 test are untouched (SC-108); every later arrival —
redelivery or correction — is still durably recorded in ``resolutions``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from stakeroute.real.conditions import run_condition
from stakeroute.storage.repository import Repository
from stakeroute.worker.settlement_runner import settle_hypothesis


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    """What one call to ``record_resolution`` actually did."""

    status: str  # 'resolved' | 'recorded' | 'redelivered'
    outcome: int
    resolution_seq: int
    settled: bool


def next_resolution_seq(repo: Repository, hypothesis_id: str) -> int:
    """1 for a hypothesis's first resolution, else one past whatever
    already exists — a deliberate correction, never an overwrite (FR-136)."""
    latest = repo.latest_resolution(hypothesis_id)
    return 1 if latest is None else latest["resolution_seq"] + 1


def record_resolution(
    repo: Repository,
    tenant_id: str,
    hypothesis_id: str,
    resolution_seq: int,
    outcome: int,
    determination: str,
    source: str,
    arrived_at_ms: int,
    check_name: str | None = None,
    check_params: dict | None = None,
    check_result: str | None = None,
    checked_at_ms: int | None = None,
) -> ResolutionResult:
    """Append one resolution row and, only on ``resolution_seq == 1``
    against a hypothesis that has not already expired, write through to
    ``outcomes`` and settle.

    ``dedup_key`` is ``f"{hypothesis_id}:{resolution_seq}"``: the same
    logical delivery redelivered with the same ``resolution_seq`` inserts
    zero rows and triggers no second settlement effect (FR-135). A
    correction always names a strictly greater ``resolution_seq``
    (``next_resolution_seq``), which is what makes it a new row rather
    than an overwrite (FR-136).
    """
    hypothesis = repo.get_hypothesis(hypothesis_id)
    is_first = resolution_seq == 1
    settled = is_first
    not_settled_reason: str | None = None
    if not is_first:
        settled = False
        not_settled_reason = "correction — the original resolution already settled"
    elif hypothesis is not None and hypothesis["status"] == "expired":
        settled = False
        not_settled_reason = (
            "hypothesis expired and stakes were already returned before this "
            "outcome arrived"
        )

    inserted = repo.insert_resolution(
        tenant_id=tenant_id,
        hypothesis_id=hypothesis_id,
        resolution_seq=resolution_seq,
        outcome=outcome,
        determination=determination,
        source=source,
        check_name=check_name,
        check_params=check_params,
        check_result=check_result,
        checked_at_ms=checked_at_ms,
        arrived_at_ms=arrived_at_ms,
        settled=settled,
        not_settled_reason=not_settled_reason,
        dedup_key=f"{hypothesis_id}:{resolution_seq}",
    )
    if not inserted:
        repo.commit()
        return ResolutionResult(
            status="redelivered",
            outcome=outcome,
            resolution_seq=resolution_seq,
            settled=False,
        )

    if settled:
        settle_hypothesis(
            repo,
            tenant_id,
            hypothesis_id,
            outcome,
            resolved_by=source,
            resolved_at_ms=arrived_at_ms,
        )
    else:
        repo.commit()

    return ResolutionResult(
        status="resolved" if settled else "recorded",
        outcome=outcome,
        resolution_seq=resolution_seq,
        settled=settled,
    )


async def resolve_hypothesis(
    repo: Repository,
    tenant_id: str,
    hypothesis_id: str,
    now_ms: int,
) -> ResolutionResult | None:
    """Re-run the condition bound to ``hypothesis_id``, if any, and record
    the outcome. Returns ``None`` if the hypothesis is unknown or bound no
    condition — that case resolves by operator confirmation instead
    (``determination='operator'``, D-017).

    Always attempts ``resolution_seq=1``: a caller invoking this more than
    once for the same hypothesis (a retried poll, a crash-restart) is a
    redelivery, not a correction, and ``record_resolution`` treats it as
    exactly that.
    """
    hypothesis = repo.get_hypothesis(hypothesis_id)
    if hypothesis is None:
        return None
    condition_name = hypothesis["condition_name"]
    if condition_name is None:
        return None

    condition_params = (
        json.loads(hypothesis["condition_params"])
        if hypothesis["condition_params"]
        else {}
    )
    check = await asyncio.to_thread(run_condition, condition_name, condition_params)
    outcome = 1 if check.result else 0

    return record_resolution(
        repo,
        tenant_id,
        hypothesis_id,
        resolution_seq=1,
        outcome=outcome,
        determination="automatic",
        source=condition_name,
        arrived_at_ms=now_ms,
        check_name=condition_name,
        check_params=condition_params,
        check_result=str(check.result),
        checked_at_ms=now_ms,
    )
