"""SC-007: every numeric field on /api/queue is reconstructible from /explain.

Closes the validation gap identified by /speckit-analyze (finding, now
resolved by T036): a viewer can trace any presented hypothesis to its
contributing agents, stakes and evidence groups in one interaction.
"""

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / f"{uuid.uuid4().hex}.db")
    import stakeroute.dashboard.main as dashboard_main

    monkeypatch.setattr(dashboard_main, "DB_PATH", db_path)
    dashboard_main._repo = None
    return TestClient(dashboard_main.app)


def test_every_queue_number_is_reconstructible_from_explain(client) -> None:
    client.post("/api/scenario/run_normal", json={"seed": 42})
    queue = client.get("/api/queue").json()
    assert queue["routed"], "expected at least one routed hypothesis"

    for entry in queue["routed"]:
        explain = client.get(f"/api/hypotheses/{entry['hypothesis_id']}/explain").json()

        assert explain["aggregated_probability"] == pytest.approx(entry["probability"])

        contributions = explain["contributions"]
        assert contributions, "explain must carry at least one contribution"

        cluster_ids = {c["evidence_cluster_id"] for c in contributions}
        assert len(cluster_ids) == entry["independent_evidence_groups"]

        discounted = sum(1 for c in contributions if c["cluster_size"] > 1)
        assert discounted == entry["discounted_report_count"]

        alpha_total = sum(c["alpha"] for c in contributions)
        assert alpha_total == pytest.approx(1.0, abs=1e-6)

        # Reconstruct the aggregate directly from the contributions and
        # confirm it matches what /api/queue displayed — the acceptance
        # test for FR-017.
        reconstructed = sum(c["alpha"] * c["probability"] for c in contributions)
        assert reconstructed == pytest.approx(entry["probability"], abs=1e-6)


def test_explain_404_for_unknown_hypothesis(client) -> None:
    client.post("/api/scenario/run_normal", json={"seed": 42})
    response = client.get("/api/hypotheses/does-not-exist/explain")
    assert response.status_code == 404
