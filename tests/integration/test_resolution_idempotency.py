"""FR-135, Principle III: a redelivered outcome inserts zero rows,
produces exactly one settlement effect, and leaves balances unchanged —
whether the redelivery is a literal retry of the same call, or a second
automatic re-check racing the first before the hypothesis's status flips.
"""

from __future__ import annotations

import pytest

from stakeroute.real import conditions
from stakeroute.real import resolution as resolution_module
from stakeroute.real.resolution import record_resolution, resolve_hypothesis

pytestmark = pytest.mark.anyio

TENANT_ID = "hostops"


def _seed(real_repo, now_ms: int) -> None:
    real_repo.upsert_hypothesis(
        hypothesis_id="h1",
        tenant_id=TENANT_ID,
        statement="disk is filling on /",
        prior_probability=0.5,
        impact_minor_units=1000,
        urgency=0.5,
        review_cost=1.0,
        deadline_ms=now_ms,
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
        expires_at_ms=now_ms + 3600_000,
        mode="real",
    )
    real_repo.commit()


async def test_redelivered_automatic_resolution_inserts_zero_rows(
    real_repo, monkeypatch
) -> None:
    now_ms = 1_000_000
    _seed(real_repo, now_ms)
    monkeypatch.setattr(
        resolution_module,
        "run_condition",
        lambda name, params: conditions.ConditionResult(result=True, detail="low"),
    )

    first = await resolve_hypothesis(real_repo, TENANT_ID, "h1", now_ms + 1000)
    agent_after_first = dict(real_repo.get_agent("host-reasoner"))

    # Same hypothesis, same resolution_seq — a redelivery, not a second
    # independent event, even though the underlying check ran again.
    second = await resolve_hypothesis(real_repo, TENANT_ID, "h1", now_ms + 2000)

    assert first is not None
    assert second is not None
    assert first.status == "resolved"
    assert second.status == "redelivered"

    rows = real_repo.list_resolutions_for_hypothesis("h1")
    assert len(rows) == 1

    settlements = real_repo.list_settlements_for_hypothesis("h1")
    assert len(settlements) == 1

    agent_after_second = dict(real_repo.get_agent("host-reasoner"))
    assert agent_after_second == agent_after_first


async def test_redelivered_record_resolution_call_is_a_pure_no_op(real_repo) -> None:
    now_ms = 1_000_000
    _seed(real_repo, now_ms)

    first = record_resolution(
        real_repo,
        TENANT_ID,
        "h1",
        resolution_seq=1,
        outcome=1,
        determination="automatic",
        source="disk_free_below",
        arrived_at_ms=now_ms,
        check_name="disk_free_below",
        check_params={"mount": "/", "pct": 5.0},
        check_result="True",
        checked_at_ms=now_ms,
    )
    second = record_resolution(
        real_repo,
        TENANT_ID,
        "h1",
        resolution_seq=1,
        outcome=1,
        determination="automatic",
        source="disk_free_below",
        arrived_at_ms=now_ms + 5,
        check_name="disk_free_below",
        check_params={"mount": "/", "pct": 5.0},
        check_result="True",
        checked_at_ms=now_ms + 5,
    )

    assert first.status == "resolved"
    assert second.status == "redelivered"
    assert len(real_repo.list_resolutions_for_hypothesis("h1")) == 1
    assert len(real_repo.list_settlements_for_hypothesis("h1")) == 1
