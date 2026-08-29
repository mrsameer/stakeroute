"""SC-108 gate: feature 001 is byte-unchanged after the real-mode migration.

This is re-run at every phase checkpoint (tasks.md notes). Two things must
both hold: (1) every feature-001 row still defaults to ``mode='sim'`` so no
existing query path changes, and (2) the seeded scenario reproduces the
exact numbers recorded in specs/001-stakeroute-attention-market/run-log.md.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from stakeroute.dashboard import main as dashboard_main


def _fresh_client() -> TestClient:
    dashboard_main._repo = None
    dashboard_main._transport.__init__()  # fresh in-memory transport per run
    return TestClient(dashboard_main.app)


def test_new_columns_default_to_sim_mode(real_repo) -> None:
    now = 1_000
    real_repo.ensure_tenant("acmepay", "AcmePay", now)
    real_repo.commit()
    real_repo.insert_event(
        event_id="e1",
        tenant_id="acmepay",
        source="metrics",
        source_event_id="s1",
        observed_at_ms=now,
        ingested_at_ms=now,
        provenance={},
        payload={},
    )
    real_repo.upsert_hypothesis(
        hypothesis_id="h1",
        tenant_id="acmepay",
        statement="stmt",
        prior_probability=0.5,
        impact_minor_units=1,
        urgency=0.5,
        review_cost=1.0,
        deadline_ms=now + 1,
        status="open",
        created_at_ms=now,
    )
    real_repo.commit()

    event = real_repo.get_event("e1")
    hypothesis = real_repo.get_hypothesis("h1")
    assert event["mode"] == "sim"
    assert hypothesis["mode"] == "sim"
    assert hypothesis["proposal_id"] is None
    assert hypothesis["condition_name"] is None


def test_seeded_scenario_reproduces_run_log_v1() -> None:
    """specs/001-.../run-log.md V1: seed 42 routes exactly 2, withholds 3,
    with h-payment-failure at rank 1."""
    client = _fresh_client()
    response = client.post("/api/scenario/run_normal", json={"seed": 42})
    assert response.status_code == 200
    body = response.json()
    assert body["seed"] == 42
    assert body["routed"] == 2
    assert body["withheld_count"] == 3

    queue = client.get("/api/queue").json()
    assert queue["attention_budget"] == 2
    assert queue["slots_used"] == 2
    assert queue["withheld_count"] == 3
    top = queue["routed"][0]
    assert top["hypothesis_id"] == "h-payment-failure"
    assert top["rank"] == 1
