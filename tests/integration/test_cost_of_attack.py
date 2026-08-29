"""The cost-of-attack report must price the run that actually happened.

A number that only agrees with its own algebra proves nothing. The central
test here takes the identity count the report predicts for majority vote,
injects exactly that many Sybils through the real ingestion path, and checks
that rank 1 flips — and that one fewer leaves it standing. The prediction is
made before the attack runs and is falsified by the pipeline, not by a
restatement of the formula.
"""

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Build an isolated dashboard client over a fresh database.

    A factory rather than a single client: the attack tests need two
    independent worlds — one flooded with N Sybils, one with N-1 — and
    Sybil identities are deterministic per target, so they cannot be
    layered into the same ledger.
    """
    import stakeroute.dashboard.main as dashboard_main

    def _make() -> TestClient:
        db_path = str(tmp_path / f"{uuid.uuid4().hex}.db")
        monkeypatch.setattr(dashboard_main, "DB_PATH", db_path)
        dashboard_main._repo = None
        client = TestClient(dashboard_main.app)
        client.post("/api/scenario/run_normal", json={"seed": 42})
        return client

    return _make


def test_report_needs_a_recorded_ranking_pass(tmp_path, monkeypatch) -> None:
    import stakeroute.dashboard.main as dashboard_main

    monkeypatch.setattr(dashboard_main, "DB_PATH", str(tmp_path / "empty.db"))
    dashboard_main._repo = None
    client = TestClient(dashboard_main.app)

    response = client.get("/api/cost_of_attack")
    assert response.status_code == 409


def test_honest_weight_matches_the_recorded_explanation(make_client) -> None:
    """The report's inputs must be the same numbers the queue explains."""
    client = make_client()
    report = client.get("/api/cost_of_attack").json()
    target_id = report["target"]["hypothesis_id"]

    explain = client.get(f"/api/hypotheses/{target_id}/explain").json()
    recorded_weight = sum(c["weight"] for c in explain["contributions"])

    assert report["target"]["honest_weight"] == pytest.approx(recorded_weight)
    assert report["target"]["probability"] == pytest.approx(
        explain["aggregated_probability"]
    )


def test_predicted_sybil_count_flips_majority_vote_and_one_fewer_does_not(
    make_client,
) -> None:
    """The money test: a falsifiable prediction, checked against the pipeline.

    Majority vote reads neither stake nor reputation, so the predicted count
    is exact — every injected identity contributes precisely one vote.
    """
    report = make_client().get("/api/cost_of_attack").json()
    predicted = report["strategies"]["majority_vote"]["identities"]
    target_id = report["target"]["hypothesis_id"]
    defender_id = report["defender"]["hypothesis_id"]
    assert predicted > 0

    exact = make_client()
    exact.post(
        "/api/scenario/inject_sybils",
        json={"count": predicted, "target": target_id},
    )
    flipped = exact.get("/api/comparison").json()["strategies"]["majority_vote"]
    assert flipped[0]["hypothesis_id"] == target_id

    one_fewer = make_client()
    one_fewer.post(
        "/api/scenario/inject_sybils",
        json={"count": predicted - 1, "target": target_id},
    )
    held = one_fewer.get("/api/comparison").json()["strategies"]["majority_vote"]
    assert held[0]["hypothesis_id"] == defender_id


def test_baselines_are_bought_with_identities_alone(make_client) -> None:
    """Neither baseline charges an attacker any capital, ever."""
    report = make_client().get("/api/cost_of_attack").json()

    for strategy in ("majority_vote", "highest_confidence"):
        cost = report["strategies"][strategy]
        assert cost["feasible"]
        assert cost["credits"] == 0
        assert cost["settlement_loss_credits"] == 0
        assert cost["reputation_per_identity"] == 0.0

    # Highest confidence is the cheapest attack surface in the system: one
    # unbacked assertion outranks a calibrated population.
    assert report["strategies"]["highest_confidence"]["identities"] == 1


def test_stakeroute_charges_capital_that_settlement_then_destroys(
    make_client,
) -> None:
    """Against StakeRoute the same attack costs credits, and loses them."""
    report = make_client().get("/api/cost_of_attack").json()
    economic = report["economic_defence_only"]

    assert economic["feasible"]
    assert economic["identities"] > 0
    assert (
        economic["credits"] == economic["identities"] * economic["stake_per_identity"]
    )

    # The attacker does not merely commit capital, it forfeits most of it
    # when the hypothesis it promoted resolves false.
    assert economic["settlement_loss_credits"] > 0
    assert economic["settlement_loss_credits"] <= economic["credits"]
    assert economic["settlement_loss_credits"] / economic["credits"] > 0.5

    for strategy in ("majority_vote", "highest_confidence"):
        assert report["strategies"][strategy]["credits"] == 0


def test_impact_weighting_is_reported_separately_from_the_market(
    make_client,
) -> None:
    """Two defences are active; the report must not conflate them.

    The headline StakeRoute figure includes the attention allocator's
    impact weighting, which is policy rather than economics. Presenting
    only that number would overstate what the market itself is worth, so
    the economics-only figure is reported alongside it and must be the
    strictly weaker claim.
    """
    report = make_client().get("/api/cost_of_attack").json()
    headline = report["strategies"]["stakeroute"]
    economic = report["economic_defence_only"]

    assert headline["required_probability"] > economic["required_probability"]
    if headline["feasible"]:
        assert headline["identities"] >= economic["identities"]


def test_frontier_prices_reputation_as_the_scarce_input(make_client) -> None:
    """Identity count must fall as earned reputation rises."""
    report = make_client().get("/api/cost_of_attack").json()
    frontier = report["frontier"]

    assert len(frontier) >= 3
    counts = [point["identities"] for point in frontier]
    reputations = [point["reputation_per_identity"] for point in frontier]

    assert reputations == sorted(reputations)
    assert counts == sorted(counts, reverse=True)
    assert counts[0] > counts[-1], (
        "a floor-reputation attacker must need strictly more identities "
        "than one that has earned standing"
    )


def test_shared_evidence_makes_the_attack_quadratically_worse(
    make_client,
) -> None:
    """The independence discount must show up as a price, not a claim."""
    client = make_client()
    distinct = client.get("/api/cost_of_attack").json()["economic_defence_only"]
    shared = client.get(
        "/api/cost_of_attack", params={"shared_evidence_cluster": True}
    ).json()["economic_defence_only"]

    assert shared["identities"] > distinct["identities"]


def test_report_rejects_a_target_that_already_leads(make_client) -> None:
    client = make_client()
    defender_id = client.get("/api/cost_of_attack").json()["defender"]["hypothesis_id"]

    response = client.get("/api/cost_of_attack", params={"target": defender_id})
    assert response.status_code == 409
