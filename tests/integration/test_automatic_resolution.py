"""FR-133, FR-148: an automatic resolution records the check performed,
when it ran, and what it returned verbatim — so the outcome is auditable
independently of any operator judgement, and settles the hypothesis.
"""

from __future__ import annotations

import pytest

from stakeroute.real import conditions
from stakeroute.real import resolution as resolution_module
from stakeroute.real.resolution import resolve_hypothesis

pytestmark = pytest.mark.anyio

TENANT_ID = "hostops"


def _seed_hypothesis(real_repo, now_ms: int, deadline_ms: int) -> None:
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
        expires_at_ms=deadline_ms + 3600_000,
        mode="real",
    )
    real_repo.commit()


async def test_resolution_records_check_name_params_result_and_checked_at(
    real_repo, monkeypatch
) -> None:
    now_ms = 1_000_000
    _seed_hypothesis(real_repo, now_ms, deadline_ms=now_ms)

    monkeypatch.setattr(
        resolution_module,
        "run_condition",
        lambda name, params: conditions.ConditionResult(
            result=True, detail="/ has 2.0% free (threshold 5.0%)"
        ),
    )

    result = await resolve_hypothesis(real_repo, TENANT_ID, "h1", now_ms + 60_000)

    assert result is not None
    assert result.status == "resolved"
    assert result.outcome == 1
    assert result.resolution_seq == 1

    row = real_repo.latest_resolution("h1")
    assert row["determination"] == "automatic"
    assert row["check_name"] == "disk_free_below"
    assert row["check_params"] == '{"mount": "/", "pct": 5.0}'
    assert row["check_result"] == "True"
    assert row["checked_at_ms"] == now_ms + 60_000
    assert row["arrived_at_ms"] == now_ms + 60_000
    assert bool(row["settled"]) is True


async def test_resolution_settles_the_hypothesis(real_repo, monkeypatch) -> None:
    now_ms = 1_000_000
    _seed_hypothesis(real_repo, now_ms, deadline_ms=now_ms)
    monkeypatch.setattr(
        resolution_module,
        "run_condition",
        lambda name, params: conditions.ConditionResult(result=False, detail="fine"),
    )

    await resolve_hypothesis(real_repo, TENANT_ID, "h1", now_ms + 1000)

    hypothesis = real_repo.get_hypothesis("h1")
    assert hypothesis["status"] == "resolved"
    settlements = real_repo.list_settlements_for_hypothesis("h1")
    assert len(settlements) == 1
    agent = real_repo.get_agent("host-reasoner")
    assert agent["staked_credits"] == 0


async def test_hypothesis_with_no_bound_condition_is_left_to_the_operator(
    real_repo,
) -> None:
    now_ms = 1_000_000
    real_repo.upsert_hypothesis(
        hypothesis_id="h2",
        tenant_id=TENANT_ID,
        statement="something odd",
        prior_probability=0.5,
        impact_minor_units=100,
        urgency=0.5,
        review_cost=1.0,
        deadline_ms=now_ms,
        status="open",
        created_at_ms=now_ms,
        mode="real",
    )
    real_repo.commit()

    result = await resolve_hypothesis(real_repo, TENANT_ID, "h2", now_ms + 1000)

    assert result is None
    assert real_repo.latest_resolution("h2") is None
    assert real_repo.get_hypothesis("h2")["status"] == "open"
