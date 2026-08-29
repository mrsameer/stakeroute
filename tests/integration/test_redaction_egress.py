"""SC-115: a five-category scan over ``events.payload`` and
``model_interactions.request`` across 100% of rows.

Redaction happens once, at ingestion (D-014) — this is the audit that the
boundary actually held, over data that went through the real pipeline
rather than over the redaction unit tests' synthetic fixtures.
"""

from __future__ import annotations

import re

import pytest

from stakeroute.model.protocol import Accepted, ModelStateReport
from stakeroute.model.recorder import ModelInteractionRecorder
from stakeroute.real.collectors import RawObservation, ingest_raw_observation
from stakeroute.real.proposal import run_proposal_cycle

pytestmark = pytest.mark.anyio

TENANT_ID = "hostops"
HOME_DIR = "/Users/quentin"
USERNAME = "quentin"

_SCAN_PATTERNS = [
    re.compile(rf"\b{re.escape(HOME_DIR)}\b"),
    re.compile(rf"\b{re.escape(USERNAME)}\b"),
    re.compile(r"[A-Z][A-Z0-9_]{2,}=(?!<redacted:env>)\S+"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
]


def _scan(text: str) -> list[str]:
    return [p.pattern for p in _SCAN_PATTERNS if p.search(text)]


class EchoModelClient:
    """Returns a proposal that (if unredacted) would leak the observations
    it was handed straight back through its rationale-like statement."""

    def __init__(self, statement: str, cited_ids: list[str]) -> None:
        self._statement = statement
        self._cited_ids = cited_ids

    async def complete(self, purpose: str, prompt: str, timeout_s: float):
        return Accepted(
            value={
                "statement": self._statement,
                "cited_observation_ids": self._cited_ids,
                "condition_name": None,
                "condition_params": None,
            },
            interaction_id="raw",
            latency_ms=1,
        )

    def state(self) -> ModelStateReport:
        return ModelStateReport(
            state="ok",
            detail="",
            unavailable_capabilities=(),
            calls_this_interval=0,
            ceiling=100,
        )


async def test_events_and_model_interactions_are_clean_across_every_row(
    real_repo,
) -> None:
    now_ms = 5_000_000

    sensitive_payloads = [
        RawObservation(
            "log:1",
            now_ms - 3000,
            {
                "level": "ERROR",
                "logger": "x",
                "message": f"user {USERNAME} hit /Users/quentin/app/error.log",
            },
            0.7,
        ),
        RawObservation(
            "log:2",
            now_ms - 2000,
            {
                "level": "ERROR",
                "logger": "x",
                "message": "DATABASE_URL=postgres://secret@db/prod",
            },
            0.6,
        ),
        RawObservation(
            "log:3",
            now_ms - 1000,
            {
                "level": "ERROR",
                "logger": "x",
                "message": "token ghp_abcdefghijklmnopqrstuvwxyz012345 leaked",
            },
            0.8,
        ),
    ]
    snapshots = []
    for raw in sensitive_payloads:
        _, snap = ingest_raw_observation(
            real_repo,
            TENANT_ID,
            "app.logs",
            raw,
            HOME_DIR,
            USERNAME,
            ingested_at_ms=now_ms,
        )
        snapshots.append(snap)
    real_repo.commit()

    # Even the model's own free-text statement is scanned — the proposal
    # pipeline never re-introduces raw content the ingestion boundary
    # already redacted.
    model_raw = EchoModelClient(
        statement="observed errors from the app.logs source",
        cited_ids=[s.event_id for s in snapshots],
    )
    model = ModelInteractionRecorder(
        real_repo, model_raw, TENANT_ID, "real", "echo-test"
    )
    await run_proposal_cycle(
        real_repo,
        TENANT_ID,
        model,
        tuple(snapshots),
        now_ms - 10_000,
        now_ms,
        now_ms,
        5.0,
    )

    events = real_repo.list_events(TENANT_ID)
    assert events
    offenders = []
    for row in events:
        hits = _scan(row["payload"])
        if hits:
            offenders.append((row["event_id"], hits))
    assert not offenders, f"unredacted content in events.payload: {offenders}"

    interactions = real_repo.list_model_interactions(TENANT_ID)
    assert interactions
    interaction_offenders = []
    for row in interactions:
        hits = _scan(row["request"])
        if hits:
            interaction_offenders.append((row["id"], hits))
    assert not interaction_offenders, (
        f"unredacted content in model_interactions.request: {interaction_offenders}"
    )
