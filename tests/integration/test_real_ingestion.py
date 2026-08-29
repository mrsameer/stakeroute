"""Tests for real-observation ingestion (FR-102, FR-104).

No new idempotency mechanism is introduced here — this is
``compute_event_id``'s existing one-second-bucket key, exercised against a
real source instead of the simulator (Principle III).
"""

from __future__ import annotations

from stakeroute.real.collectors import RawObservation, ingest_raw_observation

HOME_DIR = "/Users/alice"
USERNAME = "alice"


def _raw(
    source_event_id: str, observed_at_ms: int, severity: float = 0.5
) -> RawObservation:
    return RawObservation(
        source_event_id=source_event_id,
        observed_at_ms=observed_at_ms,
        payload={"metric": "cpu_pct", "value": 91.0},
        severity=severity,
    )


def test_real_provenance_and_timestamps_are_recorded(real_repo) -> None:
    is_new, snapshot = ingest_raw_observation(
        real_repo,
        tenant_id="hostops",
        source_id="host.metrics",
        raw=_raw("cpu:1", 1_000_000),
        home_dir=HOME_DIR,
        username=USERNAME,
        ingested_at_ms=1_000_100,
    )
    assert is_new
    real_repo.commit()

    row = real_repo.get_event(snapshot.event_id)
    assert row is not None
    assert row["mode"] == "real"
    assert row["tenant_id"] == "hostops"
    assert row["source"] == "host.metrics"
    assert row["observed_at_ms"] == 1_000_000
    assert row["ingested_at_ms"] == 1_000_100


def test_source_event_id_is_stable_across_repeated_polls(real_repo) -> None:
    raw = _raw("cpu:same-second", 1_000_000)
    _, first = ingest_raw_observation(
        real_repo, "hostops", "host.metrics", raw, HOME_DIR, USERNAME
    )
    _, second = ingest_raw_observation(
        real_repo, "hostops", "host.metrics", raw, HOME_DIR, USERNAME
    )
    assert first.event_id == second.event_id


def test_redelivery_within_the_same_second_produces_no_duplicate_effect(
    real_repo,
) -> None:
    raw = _raw("cpu:redelivered", 1_000_000)
    is_new_1, snapshot_1 = ingest_raw_observation(
        real_repo, "hostops", "host.metrics", raw, HOME_DIR, USERNAME
    )
    real_repo.commit()
    is_new_2, snapshot_2 = ingest_raw_observation(
        real_repo, "hostops", "host.metrics", raw, HOME_DIR, USERNAME
    )
    real_repo.commit()

    assert is_new_1 is True
    assert is_new_2 is False
    assert snapshot_1.event_id == snapshot_2.event_id
    assert real_repo.count_events("hostops") == 1


def test_out_of_order_arrival_is_recorded_by_its_own_observed_time(real_repo) -> None:
    later = _raw("cpu:later", 2_000_000)
    earlier = _raw("cpu:earlier", 1_000_000)

    ingest_raw_observation(
        real_repo, "hostops", "host.metrics", later, HOME_DIR, USERNAME
    )
    real_repo.commit()
    ingest_raw_observation(
        real_repo, "hostops", "host.metrics", earlier, HOME_DIR, USERNAME
    )
    real_repo.commit()

    assert real_repo.count_events("hostops") == 2
    rows = real_repo.list_events("hostops", since_ms=0)
    observed_times = sorted(row["observed_at_ms"] for row in rows)
    assert observed_times == [1_000_000, 2_000_000]


def test_past_dated_arrival_does_not_duplicate_an_existing_record(real_repo) -> None:
    raw = _raw("cpu:past-dated", 500_000)
    ingest_raw_observation(
        real_repo, "hostops", "host.metrics", raw, HOME_DIR, USERNAME
    )
    real_repo.commit()

    # The same underlying observation, redelivered "late" — same
    # source_event_id and second-bucket, so it must still collapse.
    is_new, _ = ingest_raw_observation(
        real_repo, "hostops", "host.metrics", raw, HOME_DIR, USERNAME
    )
    real_repo.commit()

    assert is_new is False
    assert real_repo.count_events("hostops") == 1


def test_ingested_payload_is_redacted_before_it_is_stored(real_repo) -> None:
    raw = RawObservation(
        source_event_id="log:1",
        observed_at_ms=1_000_000,
        payload={"level": "ERROR", "logger": "x", "message": "user alice hit an error"},
        severity=0.8,
    )
    _, snapshot = ingest_raw_observation(
        real_repo, "hostops", "app.logs", raw, HOME_DIR, USERNAME
    )
    real_repo.commit()

    row = real_repo.get_event(snapshot.event_id)
    import json

    payload = json.loads(row["payload"])
    assert "alice" not in payload["message"]
