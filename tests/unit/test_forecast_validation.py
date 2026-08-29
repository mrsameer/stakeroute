"""Tests that forecast rejections are both correctly classified and
durably recorded (FR-116) — not merely validated in isolation, which
tests/unit/test_model_validation.py already covers for the pure
validator. This file is about the full path: ``real/reasoners.py``
routing a rejection into ``rejected_forecasts``.
"""

from __future__ import annotations

import pytest

from stakeroute.core.types import ObservationSnapshot
from stakeroute.model.protocol import Accepted, ModelStateReport
from stakeroute.model.recorder import ModelInteractionRecorder
from stakeroute.real.reasoners import run_agent_forecast
from stakeroute.real.scopes import EvidenceAccessScope, EvidenceBundle

pytestmark = pytest.mark.anyio

TENANT_ID = "hostops"


class StubModelClient:
    def __init__(self, response: dict) -> None:
        self._response = response

    async def complete(self, purpose: str, prompt: str, timeout_s: float):
        return Accepted(value=self._response, interaction_id="raw", latency_ms=2)

    def state(self) -> ModelStateReport:
        return ModelStateReport(
            state="ok",
            detail="",
            unavailable_capabilities=(),
            calls_this_interval=0,
            ceiling=100,
        )


def _bundle(now_ms: int) -> EvidenceBundle:
    scope = EvidenceAccessScope(
        agent_id="host-reasoner", source_ids=frozenset({"host.metrics"}), label="host"
    )
    obs = ObservationSnapshot(
        event_id="e1",
        source="host.metrics",
        observed_at_ms=now_ms - 1000,
        payload={"metric": "cpu_pct", "value": 90},
        severity=0.0,
    )
    return EvidenceBundle(
        agent_id="host-reasoner",
        hypothesis_id="h1",
        hypothesis_statement="CPU is saturated",
        scope=scope,
        observations=(obs,),
        built_at_ms=now_ms,
    )


async def _seed_agent(real_repo, available_credits: int) -> None:
    real_repo.upsert_agent(
        agent_id="host-reasoner",
        tenant_id=TENANT_ID,
        display_name="Host Reasoner",
        reputation=0.5,
        available_credits=available_credits,
        staked_credits=0,
        attested=True,
        created_at_ms=0,
    )
    real_repo.commit()


async def test_out_of_range_probability_is_rejected_and_recorded(real_repo) -> None:
    await _seed_agent(real_repo, 100)
    model = ModelInteractionRecorder(
        real_repo,
        StubModelClient({"probability": 1.5, "stake": 10, "rationale": "cpu is high"}),
        TENANT_ID,
        "real",
        "stub",
    )
    now_ms = 1_000_000
    result = await run_agent_forecast(
        real_repo, TENANT_ID, _bundle(now_ms), model, now_ms, 5.0, now_ms + 60_000
    )
    assert result is None
    rejected = real_repo.list_rejected_forecasts(TENANT_ID)
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "PROBABILITY_OUT_OF_RANGE"
    assert real_repo.list_forecasts_for_hypothesis("h1") == []


async def test_stake_out_of_range_is_rejected_and_recorded(real_repo) -> None:
    await _seed_agent(real_repo, 100)
    model = ModelInteractionRecorder(
        real_repo,
        StubModelClient(
            {"probability": 0.7, "stake": 9999, "rationale": "cpu is high"}
        ),
        TENANT_ID,
        "real",
        "stub",
    )
    now_ms = 1_000_000
    result = await run_agent_forecast(
        real_repo, TENANT_ID, _bundle(now_ms), model, now_ms, 5.0, now_ms + 60_000
    )
    assert result is None
    rejected = real_repo.list_rejected_forecasts(TENANT_ID)
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "STAKE_OUT_OF_RANGE"


async def test_stake_exceeding_available_credits_is_rejected_and_recorded(
    real_repo,
) -> None:
    await _seed_agent(real_repo, 5)  # far below the stake requested
    model = ModelInteractionRecorder(
        real_repo,
        StubModelClient({"probability": 0.7, "stake": 20, "rationale": "cpu is high"}),
        TENANT_ID,
        "real",
        "stub",
    )
    now_ms = 1_000_000
    result = await run_agent_forecast(
        real_repo, TENANT_ID, _bundle(now_ms), model, now_ms, 5.0, now_ms + 60_000
    )
    assert result is None
    rejected = real_repo.list_rejected_forecasts(TENANT_ID)
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "INSUFFICIENT_CREDITS"

    # No credits moved for a rejected forecast.
    agent = real_repo.get_agent("host-reasoner")
    assert agent["available_credits"] == 5
    assert agent["staked_credits"] == 0


async def test_a_valid_forecast_is_not_recorded_as_rejected(real_repo) -> None:
    await _seed_agent(real_repo, 100)
    real_repo.upsert_hypothesis(
        hypothesis_id="h1",
        tenant_id=TENANT_ID,
        statement="CPU is saturated",
        prior_probability=0.5,
        impact_minor_units=1,
        urgency=0.5,
        review_cost=1.0,
        deadline_ms=2_000_000,
        status="open",
        created_at_ms=0,
    )
    real_repo.commit()
    model = ModelInteractionRecorder(
        real_repo,
        StubModelClient({"probability": 0.7, "stake": 20, "rationale": "cpu is high"}),
        TENANT_ID,
        "real",
        "stub",
    )
    now_ms = 1_000_000
    result = await run_agent_forecast(
        real_repo, TENANT_ID, _bundle(now_ms), model, now_ms, 5.0, now_ms + 60_000
    )
    assert result is not None
    assert real_repo.list_rejected_forecasts(TENANT_ID) == []
    agent = real_repo.get_agent("host-reasoner")
    assert agent["available_credits"] == 80
    assert agent["staked_credits"] == 20
