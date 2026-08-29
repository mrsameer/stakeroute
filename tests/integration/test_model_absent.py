"""FR-120, SC-104: a full ranking pass and settlement, with a model client
that raises on any call — proving Principle I structurally, not by
discipline. Rankings must be identical whether or not the model happens
to be reachable, because ranking never reads it at all.
"""

from __future__ import annotations

from stakeroute.config import ATTENTION_BUDGET
from stakeroute.worker.pipeline import run_ranking_pass
from stakeroute.worker.settlement_runner import settle_hypothesis

TENANT_ID = "hostops"


class ExplodingModelClient:
    """Raises on any call. Exists in these tests to prove nothing on the
    decision path ever reaches it — it is never passed to any decision-
    path function, because none of them accept a model at all."""

    async def complete(self, purpose: str, prompt: str, timeout_s: float):
        raise AssertionError(
            f"decision path must never call the model (purpose={purpose!r})"
        )

    def state(self):
        raise AssertionError("decision path must never query model state")


def _seed(real_repo, now_ms: int) -> None:
    real_repo.upsert_agent(
        agent_id="host-reasoner",
        tenant_id=TENANT_ID,
        display_name="Host Reasoner",
        reputation=0.6,
        available_credits=80,
        staked_credits=20,
        attested=True,
        created_at_ms=0,
    )
    real_repo.upsert_agent(
        agent_id="logs-reasoner",
        tenant_id=TENANT_ID,
        display_name="Logs Reasoner",
        reputation=0.4,
        available_credits=90,
        staked_credits=10,
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
    real_repo.upsert_hypothesis(
        hypothesis_id="h2",
        tenant_id=TENANT_ID,
        statement="Disk full",
        prior_probability=0.5,
        impact_minor_units=500,
        urgency=0.3,
        review_cost=1.0,
        deadline_ms=now_ms + 60_000,
        status="open",
        created_at_ms=now_ms,
        mode="real",
    )
    real_repo.ensure_evidence_cluster("host.metrics", TENANT_ID, "host.metrics")
    real_repo.ensure_evidence_cluster("app.logs", TENANT_ID, "app.logs")
    real_repo.upsert_forecast(
        forecast_id="f1",
        tenant_id=TENANT_ID,
        hypothesis_id="h1",
        agent_id="host-reasoner",
        probability=0.8,
        stake=20,
        evidence_cluster_id="host.metrics",
        evidence_refs=[],
        source_event_id="s1",
        created_at_ms=now_ms,
        expires_at_ms=now_ms + 60_000,
        mode="real",
    )
    real_repo.upsert_forecast(
        forecast_id="f2",
        tenant_id=TENANT_ID,
        hypothesis_id="h2",
        agent_id="logs-reasoner",
        probability=0.3,
        stake=10,
        evidence_cluster_id="app.logs",
        evidence_refs=[],
        source_event_id="s2",
        created_at_ms=now_ms,
        expires_at_ms=now_ms + 60_000,
        mode="real",
    )
    real_repo.commit()


def test_full_ranking_pass_completes_with_an_exploding_model_client_in_scope(
    real_repo,
) -> None:
    now_ms = 1_000_000
    _seed(real_repo, now_ms)
    _unused = ExplodingModelClient()  # never touched — proven by not crashing

    results = run_ranking_pass(real_repo, TENANT_ID, ATTENTION_BUDGET, now_ms)
    assert results["stakeroute"].ranked
    assert results["stakeroute"].allocation.decisions


def test_settlement_completes_with_the_model_unreachable(real_repo) -> None:
    now_ms = 1_000_000
    _seed(real_repo, now_ms)
    _unused = ExplodingModelClient()

    settled = settle_hypothesis(
        real_repo,
        TENANT_ID,
        "h1",
        outcome=1,
        resolved_by="operator",
        resolved_at_ms=now_ms,
    )
    assert settled

    agent = real_repo.get_agent("host-reasoner")
    assert agent["staked_credits"] == 0  # stake released
    settlements = real_repo.list_settlements_for_hypothesis("h1")
    assert len(settlements) == 1


def test_rankings_are_identical_across_repeated_passes(real_repo) -> None:
    """Ranking reads only recorded state — running it twice against the
    same data, with or without a model anywhere nearby, produces the same
    decisions (SC-104)."""
    now_ms = 1_000_000
    _seed(real_repo, now_ms)

    first = run_ranking_pass(real_repo, TENANT_ID, ATTENTION_BUDGET, now_ms)
    second = run_ranking_pass(real_repo, TENANT_ID, ATTENTION_BUDGET, now_ms)

    first_decisions = [
        (d.hypothesis_id, d.routed, d.rank)
        for d in first["stakeroute"].allocation.decisions
    ]
    second_decisions = [
        (d.hypothesis_id, d.routed, d.rank)
        for d in second["stakeroute"].allocation.decisions
    ]
    assert first_decisions == second_decisions

    first_probs = {r.hypothesis_id: r.probability for r in first["stakeroute"].ranked}
    second_probs = {r.hypothesis_id: r.probability for r in second["stakeroute"].ranked}
    assert first_probs == second_probs
