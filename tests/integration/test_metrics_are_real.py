"""FR-034: every metric traces to recorded rows; nothing is fabricated.

Before any run, every metric MUST read null (not a placeholder zero).
After a run with a resolved outcome, every metric MUST be non-null and
its measured_over count MUST be a real, positive count of recorded rows.
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
    dashboard_main._run_id = None
    return TestClient(dashboard_main.app)


def test_metrics_are_null_before_any_run(client) -> None:
    metrics = client.get("/api/metrics").json()
    assert metrics["precision_at_k"] is None
    assert metrics["false_escalation_rate"] is None
    assert metrics["time_to_attention_ms"] is None
    assert metrics["mean_brier_score"] is None
    assert metrics["run_id"] is None
    assert metrics["measured_over_events"] == 0


def test_metrics_are_populated_and_traceable_after_a_resolved_run(client) -> None:
    run_response = client.post("/api/scenario/run_normal", json={"seed": 42})
    assert run_response.status_code == 200

    # Before any resolution, precision/false-escalation/TTA/Brier are still
    # unmeasured — routing alone does not manufacture a ground-truth signal.
    metrics_before = client.get("/api/metrics").json()
    assert metrics_before["precision_at_k"] is None
    assert metrics_before["mean_brier_score"] is None
    assert metrics_before["events_per_second"] is not None  # events exist already
    assert metrics_before["run_id"] is not None

    client.post(
        "/api/scenario/resolve",
        json={"hypothesis_id": "h-payment-failure", "outcome": 1},
    )
    client.post(
        "/api/scenario/resolve",
        json={"hypothesis_id": "h-database-saturation", "outcome": 0},
    )

    metrics_after = client.get("/api/metrics").json()

    assert metrics_after["precision_at_k"] is not None
    assert metrics_after["measured_over"]["precision_at_k"] > 0
    assert metrics_after["false_escalation_rate"] is not None
    assert metrics_after["measured_over"]["false_escalation_rate"] > 0
    assert metrics_after["time_to_attention_ms"] is not None
    assert metrics_after["mean_brier_score"] is not None
    assert metrics_after["measured_over"]["mean_brier_score"] > 0
    assert metrics_after["events_per_second"] is not None
    assert metrics_after["measured_over_events"] > 0

    # Traceability: precision_at_k must actually reconstruct from /api/queue
    # and /api/comparison ground truth rather than being a stored constant.
    queue = client.get("/api/queue").json()
    comparison = client.get("/api/comparison").json()
    routed_ids = {entry["hypothesis_id"] for entry in queue["routed"]}
    resolved_true = {
        hid for hid, outcome in comparison["ground_truth"].items() if outcome == 1
    }
    expected_precision = len(routed_ids & resolved_true) / len(routed_ids)
    assert metrics_after["precision_at_k"] == pytest.approx(expected_precision)


def test_no_hardcoded_constant_in_metrics_module() -> None:
    """FR-034: metrics.py must not contain a literal benchmark figure such
    as a fabricated precision or throughput constant standing in for a
    computed value."""
    import inspect

    import stakeroute.metrics as metrics_module

    source = inspect.getsource(metrics_module)
    # Deliberately excludes unit-conversion constants (1000.0 ms->s, the
    # 0.001 floor-duration guard) — those are arithmetic, not benchmark
    # figures standing in for a measurement.
    forbidden_literals = ["0.95", "0.99", "99.9", "1234"]
    for literal in forbidden_literals:
        assert literal not in source, f"suspicious hard-coded figure: {literal}"
