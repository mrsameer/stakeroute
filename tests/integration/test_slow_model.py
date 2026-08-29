"""FR-121, SC-105: a slow model must not delay the ranking pass, and a
call through the recorder must never wait past its configured timeout —
this asserts the architecture, not the tuning.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from stakeroute.config import ATTENTION_BUDGET
from stakeroute.model.recorder import ModelInteractionRecorder
from stakeroute.real.reasoners import ForecastRejection, forecast
from stakeroute.real.scopes import EvidenceAccessScope, EvidenceBundle
from stakeroute.worker.pipeline import run_ranking_pass

pytestmark = pytest.mark.anyio

TENANT_ID = "hostops"


class NeverRespondingModelClient:
    """Ignores ``timeout_s`` entirely — the recording boundary's own
    backstop must save us, not this client's discipline."""

    async def complete(self, purpose: str, prompt: str, timeout_s: float):
        await asyncio.sleep(timeout_s + 30)
        raise AssertionError("should have been cancelled by the recorder's timeout")

    def state(self):
        from stakeroute.model.protocol import ModelStateReport

        return ModelStateReport(
            state="ok",
            detail="",
            unavailable_capabilities=(),
            calls_this_interval=0,
            ceiling=100,
        )


def _seed_ranking_data(real_repo, now_ms: int) -> None:
    real_repo.upsert_agent(
        agent_id="host-reasoner",
        tenant_id=TENANT_ID,
        display_name="Host Reasoner",
        reputation=0.5,
        available_credits=100,
        staked_credits=0,
        attested=True,
        created_at_ms=0,
    )
    real_repo.upsert_hypothesis(
        hypothesis_id="h1",
        tenant_id=TENANT_ID,
        statement="CPU saturated",
        prior_probability=0.5,
        impact_minor_units=1000,
        urgency=0.5,
        review_cost=1.0,
        deadline_ms=now_ms + 60_000,
        status="open",
        created_at_ms=now_ms,
        mode="real",
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
        expires_at_ms=now_ms + 60_000,
        mode="real",
    )
    real_repo.commit()


def test_ranking_pass_duration_is_unaffected_by_a_slow_model_existing(
    real_repo,
) -> None:
    now_ms = 1_000_000
    _seed_ranking_data(real_repo, now_ms)
    # NeverRespondingModelClient exists but run_ranking_pass has no
    # parameter to receive it — its presence in this test's scope proves
    # nothing about ranking's behaviour except that it cannot slow it down.
    _unused = NeverRespondingModelClient()

    started = time.monotonic()
    run_ranking_pass(real_repo, TENANT_ID, ATTENTION_BUDGET, now_ms)
    elapsed_s = time.monotonic() - started

    assert elapsed_s < 1.0, (
        f"ranking pass took {elapsed_s:.2f}s — far too slow to be model-free"
    )


async def test_forecast_call_never_waits_past_its_timeout(real_repo) -> None:
    scope = EvidenceAccessScope(
        agent_id="host-reasoner", source_ids=frozenset({"host.metrics"}), label="host"
    )
    bundle = EvidenceBundle(
        agent_id="host-reasoner",
        hypothesis_id="h1",
        hypothesis_statement="CPU saturated",
        scope=scope,
        observations=(),
        built_at_ms=0,
    )
    model = ModelInteractionRecorder(
        real_repo, NeverRespondingModelClient(), TENANT_ID, "real", "slow-test"
    )

    started = time.monotonic()
    result = await forecast(bundle, model, available_credits=100, timeout_s=0.2)
    elapsed_s = time.monotonic() - started

    assert elapsed_s < 2.0, (
        f"forecast() took {elapsed_s:.2f}s against a 0.2s timeout — the "
        "recorder's backstop did not fire"
    )
    assert isinstance(result, ForecastRejection)
    assert result.reason == "TIMEOUT"

    interactions = real_repo.list_model_interactions(TENANT_ID)
    assert len(interactions) == 1
    assert interactions[0]["accepted"] == 0
    assert interactions[0]["rejection_reason"] == "TIMEOUT"
    assert interactions[0]["response"] is None
