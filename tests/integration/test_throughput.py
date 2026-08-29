"""SC-011: the pipeline sustains the scenario's signal volume without the
operator queue falling behind. Closes the validation gap identified by
/speckit-analyze — T083 defines the measurable threshold in spec.md;
this test is what T062 said would already implement it.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

# The agreed bound (see spec.md SC-011): the ranking pass must not trail
# the newest ingested event by more than 500ms in the synchronous,
# in-process demo path. A real JetStream-backed worker (Phase 7) has its
# own network and consumer-poll latency and is measured separately.
RANKING_LAG_BOUND_MS = 500


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / f"{uuid.uuid4().hex}.db")
    import stakeroute.dashboard.main as dashboard_main

    monkeypatch.setattr(dashboard_main, "DB_PATH", db_path)
    dashboard_main._repo = None
    return TestClient(dashboard_main.app)


def test_sustained_ingest_rate_and_ranking_lag(client) -> None:
    response = client.post("/api/scenario/run_normal", json={"seed": 42})
    assert response.status_code == 200

    metrics = client.get("/api/metrics").json()

    assert metrics["events_per_second"] is not None
    assert metrics["events_per_second"] >= 1000, (
        f"ingest rate {metrics['events_per_second']:.1f}/s fell below the "
        "1,000 events/sec target"
    )
    assert metrics["measured_over_events"] > 0

    assert metrics["ranking_pass_lag_ms"] is not None
    assert metrics["ranking_pass_lag_ms"] <= RANKING_LAG_BOUND_MS, (
        f"ranking pass lagged the newest event by "
        f"{metrics['ranking_pass_lag_ms']}ms, exceeding the "
        f"{RANKING_LAG_BOUND_MS}ms bound"
    )
