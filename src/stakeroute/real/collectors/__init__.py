"""The collector protocol, observation envelope, liveness tracking, and the
volume policy (Principle III, FR-102, FR-104, FR-105, FR-141).

No new idempotency mechanism is introduced here — every observation reuses
``compute_event_id``'s existing one-second-bucket key, exercised against a
real source instead of the simulator.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from stakeroute.core.types import ObservationSnapshot, compute_event_id
from stakeroute.real.redaction import redact_event
from stakeroute.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class RawObservation:
    """One observation as a collector produces it, before redaction."""

    source_event_id: str
    observed_at_ms: int
    payload: dict
    severity: float


@dataclass(frozen=True, slots=True)
class CollectorPollResult:
    """What one collector poll returns: either fresh observations, or an
    absence marker (FR-141) — never both, and never silently neither."""

    observations: tuple[RawObservation, ...] = ()
    absent: bool = False
    absent_reason: str | None = None


class Collector(Protocol):
    """Every collector polls on its own interval and reports its own
    absence — a missing binary or runtime is a recorded state, not a raised
    exception (D-015)."""

    source_id: str
    display_name: str
    silence_threshold_ms: int

    async def poll(self) -> CollectorPollResult:
        """Return newly observed raw observations since the last poll, or
        report this source as absent."""
        ...


# -- Ingestion --------------------------------------------------------------


def ingest_raw_observation(
    repo: Repository,
    tenant_id: str,
    source_id: str,
    raw: RawObservation,
    home_dir: str,
    username: str,
    mode: str = "real",
    ingested_at_ms: int | None = None,
) -> tuple[bool, ObservationSnapshot]:
    """Redact, compute the idempotency id, and durably write one
    observation. Returns ``(is_new, snapshot)`` — a redelivery (same
    ``source_event_id`` within the same second bucket) inserts no new row
    and produces no duplicate economic effect (Principle III)."""
    redaction = redact_event(source_id, raw.payload, home_dir, username)
    event_id = compute_event_id(
        tenant_id, source_id, raw.source_event_id, raw.observed_at_ms
    )
    resolved_ingested_at_ms = (
        ingested_at_ms if ingested_at_ms is not None else int(time.time() * 1000)
    )
    is_new = repo.insert_event(
        event_id=event_id,
        tenant_id=tenant_id,
        source=source_id,
        source_event_id=raw.source_event_id,
        observed_at_ms=raw.observed_at_ms,
        ingested_at_ms=resolved_ingested_at_ms,
        provenance={
            "collector": source_id,
            "mode": mode,
            "redactions_applied": list(redaction.rules_fired),
        },
        payload=redaction.payload,
        mode=mode,
    )
    snapshot = ObservationSnapshot(
        event_id=event_id,
        source=source_id,
        observed_at_ms=raw.observed_at_ms,
        payload=redaction.payload,
        severity=raw.severity,
    )
    return is_new, snapshot


# -- Source liveness (FR-141) -------------------------------------------------


def next_liveness_state(
    current_state: str,
    observed_this_poll: bool,
    elapsed_since_activity_ms: int,
    silence_threshold_ms: int,
) -> str:
    """The whole state machine, as a pure function over one poll's result.

    ``absent`` is terminal for a run — the binary or runtime a source
    depends on does not appear part-way through. Any observation returns a
    source to ``live`` from anywhere else. ``live`` moves to ``quiet`` after
    a single empty poll; ``quiet`` moves to ``silent`` once
    ``elapsed_since_activity_ms`` reaches the threshold. A silent source and
    a quiet one must never render the same value.
    """
    if current_state == "absent":
        return "absent"
    if observed_this_poll:
        return "live"
    if current_state == "live":
        return "quiet"
    if elapsed_since_activity_ms >= silence_threshold_ms:
        return "silent"
    return current_state


def update_source_liveness(
    repo: Repository,
    tenant_id: str,
    source_id: str,
    display_name: str,
    silence_threshold_ms: int,
    now_ms: int,
    poll_result: CollectorPollResult,
) -> None:
    """Apply one poll's result to ``observation_sources`` — startup absence
    detection and every subsequent poll go through this one function."""
    existing = repo.get_observation_source(source_id)
    current_state = existing["state"] if existing else "quiet"
    last_seen_ms = existing["last_seen_ms"] if existing else None
    set_aside_count = existing["set_aside_count"] if existing else 0

    if poll_result.absent:
        repo.upsert_observation_source(
            source_id=source_id,
            tenant_id=tenant_id,
            display_name=display_name,
            state="absent",
            last_seen_ms=last_seen_ms,
            silence_threshold_ms=silence_threshold_ms,
            absent_reason=poll_result.absent_reason,
            set_aside_count=set_aside_count,
            updated_at_ms=now_ms,
        )
        return

    observed_this_poll = bool(poll_result.observations)
    if observed_this_poll:
        new_last_seen_ms = now_ms
        elapsed_since_activity_ms = 0
    else:
        new_last_seen_ms = last_seen_ms
        elapsed_since_activity_ms = (
            now_ms - last_seen_ms if last_seen_ms is not None else 0
        )

    new_state = next_liveness_state(
        current_state,
        observed_this_poll,
        elapsed_since_activity_ms,
        silence_threshold_ms,
    )
    repo.upsert_observation_source(
        source_id=source_id,
        tenant_id=tenant_id,
        display_name=display_name,
        state=new_state,
        last_seen_ms=new_last_seen_ms,
        silence_threshold_ms=silence_threshold_ms,
        absent_reason=None,
        set_aside_count=set_aside_count,
        updated_at_ms=now_ms,
    )


# -- Volume policy (FR-105) ---------------------------------------------------


def apply_volume_policy(
    observations: tuple[ObservationSnapshot, ...], limit: int
) -> tuple[tuple[ObservationSnapshot, ...], int]:
    """Retain the highest-severity observation per ``(source, subject)``
    within the interval, then cap the result at ``limit``.

    Every observation is already durably written to ``events`` before this
    runs (Principle IV: suppression must be visible, never silent) — this
    function only decides what gets *proposed over*. Returns
    ``(retained, set_aside_count)``.
    """
    best_per_subject: dict[tuple[str, str], ObservationSnapshot] = {}
    for obs in observations:
        subject = str(obs.payload.get("mount") or obs.payload.get("process_name") or "")
        key = (obs.source, subject)
        current_best = best_per_subject.get(key)
        if current_best is None or obs.severity > current_best.severity:
            best_per_subject[key] = obs

    deduped = tuple(best_per_subject.values())
    set_aside = len(observations) - len(deduped)

    if len(deduped) > limit:
        ranked = sorted(deduped, key=lambda o: (-o.severity, o.event_id))
        retained = tuple(ranked[:limit])
        set_aside += len(deduped) - limit
    else:
        retained = deduped

    return retained, set_aside
