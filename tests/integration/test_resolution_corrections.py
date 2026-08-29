"""FR-136, FR-138: a correction is a new ``resolution_seq``, never an
overwrite of the original, and an outcome arriving after a hypothesis
already expired — its stakes already returned — is recorded as
unsettled with a stated reason rather than silently re-settling them.
"""

from __future__ import annotations

import pytest

from stakeroute.real import conditions
from stakeroute.real import resolution as resolution_module
from stakeroute.real.resolution import (
    next_resolution_seq,
    record_resolution,
    resolve_hypothesis,
)
from stakeroute.worker.settlement_runner import expire_overdue_hypotheses

pytestmark = pytest.mark.anyio

TENANT_ID = "hostops"


def _seed(real_repo, now_ms: int, deadline_ms: int) -> None:
    real_repo.upsert_hypothesis(
        hypothesis_id="h1",
        tenant_id=TENANT_ID,
        statement="disk is filling on /",
        prior_probability=0.5,
        impact_minor_units=1000,
        urgency=0.5,
        review_cost=1.0,
        deadline_ms=deadline_ms,
        status="open",
        created_at_ms=now_ms,
        mode="real",
        condition_name="disk_free_below",
        condition_params={"mount": "/", "pct": 5.0},
    )
    real_repo.upsert_agent(
        agent_id="host-reasoner",
        tenant_id=TENANT_ID,
        display_name="Host Reasoner",
        reputation=0.5,
        available_credits=90,
        staked_credits=10,
        attested=True,
        created_at_ms=0,
    )
    real_repo.ensure_evidence_cluster("host.metrics", TENANT_ID, "host.metrics")
    real_repo.upsert_forecast(
        forecast_id="f1",
        tenant_id=TENANT_ID,
        hypothesis_id="h1",
        agent_id="host-reasoner",
        probability=0.8,
        stake=10,
        evidence_cluster_id="host.metrics",
        evidence_refs=[],
        source_event_id="s1",
        created_at_ms=now_ms,
        expires_at_ms=deadline_ms + 3_600_000,
        mode="real",
    )
    real_repo.commit()


async def test_operator_correction_adds_a_new_seq_without_touching_the_first(
    real_repo, monkeypatch
) -> None:
    now_ms = 1_000_000
    _seed(real_repo, now_ms, deadline_ms=now_ms)
    monkeypatch.setattr(
        resolution_module,
        "run_condition",
        lambda name, params: conditions.ConditionResult(result=True, detail="low"),
    )

    first = await resolve_hypothesis(real_repo, TENANT_ID, "h1", now_ms + 1000)
    assert first is not None
    assert first.status == "resolved"
    assert first.outcome == 1
    settlements_after_first = real_repo.list_settlements_for_hypothesis("h1")
    agent_after_first = dict(real_repo.get_agent("host-reasoner"))

    seq = next_resolution_seq(real_repo, "h1")
    assert seq == 2
    correction = record_resolution(
        real_repo,
        TENANT_ID,
        "h1",
        resolution_seq=seq,
        outcome=0,
        determination="operator",
        source="operator",
        arrived_at_ms=now_ms + 2000,
    )

    assert correction.status == "recorded"
    assert correction.settled is False

    rows = real_repo.list_resolutions_for_hypothesis("h1")
    assert len(rows) == 2
    assert rows[0]["resolution_seq"] == 1
    assert rows[0]["outcome"] == 1  # untouched by the correction
    assert rows[1]["resolution_seq"] == 2
    assert rows[1]["outcome"] == 0
    assert rows[1]["determination"] == "operator"
    assert "correction" in rows[1]["not_settled_reason"]

    # The correction is recorded but never re-settles.
    assert real_repo.list_settlements_for_hypothesis("h1") == settlements_after_first
    assert dict(real_repo.get_agent("host-reasoner")) == agent_after_first


async def test_outcome_arriving_after_expiry_is_recorded_unsettled(
    real_repo, monkeypatch
) -> None:
    now_ms = 1_000_000
    deadline_ms = now_ms + 1000
    _seed(real_repo, now_ms, deadline_ms=deadline_ms)

    expired_ids = expire_overdue_hypotheses(real_repo, TENANT_ID, deadline_ms + 1)
    assert expired_ids == ["h1"]
    agent_after_expiry = dict(real_repo.get_agent("host-reasoner"))
    assert agent_after_expiry["staked_credits"] == 0  # stake already returned

    monkeypatch.setattr(
        resolution_module,
        "run_condition",
        lambda name, params: conditions.ConditionResult(result=True, detail="low"),
    )
    result = await resolve_hypothesis(real_repo, TENANT_ID, "h1", deadline_ms + 5000)

    assert result is not None
    assert result.status == "recorded"
    assert result.settled is False

    row = real_repo.latest_resolution("h1")
    assert bool(row["settled"]) is False
    assert row["not_settled_reason"] is not None
    assert "expired" in row["not_settled_reason"]

    # No settlement was created, and the stake that was already returned
    # at expiry is not touched again.
    assert real_repo.list_settlements_for_hypothesis("h1") == []
    assert dict(real_repo.get_agent("host-reasoner")) == agent_after_expiry
