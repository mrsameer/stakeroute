"""Tests for deterministic duplicate-hypothesis detection (FR-110, D-023)."""

from __future__ import annotations

from stakeroute.core.duplicates import ProposalFingerprint, find_duplicate

WINDOW_MS = 5 * 60 * 1000
THRESHOLD = 0.6


def _fp(
    id_: str,
    condition_name: str | None = None,
    condition_params: dict | None = None,
    cited: frozenset[str] = frozenset(),
    created_at_ms: int = 1_000_000,
) -> ProposalFingerprint:
    return ProposalFingerprint(
        id=id_,
        condition_name=condition_name,
        condition_params=condition_params,
        cited_observation_ids=cited,
        created_at_ms=created_at_ms,
    )


def test_exact_condition_match_merges() -> None:
    candidate = _fp(
        "p2",
        condition_name="disk_free_below",
        condition_params={"mount": "/", "pct": 10},
    )
    existing = (
        _fp(
            "p1",
            condition_name="disk_free_below",
            condition_params={"mount": "/", "pct": 10},
        ),
    )
    match = find_duplicate(candidate, existing, THRESHOLD, WINDOW_MS)
    assert match is not None
    assert match.kind == "merge"
    assert match.other_id == "p1"
    assert match.basis


def test_different_condition_params_does_not_merge() -> None:
    candidate = _fp(
        "p2",
        condition_name="disk_free_below",
        condition_params={"mount": "/", "pct": 10},
    )
    existing = (
        _fp(
            "p1",
            condition_name="disk_free_below",
            condition_params={"mount": "/data", "pct": 10},
        ),
    )
    match = find_duplicate(candidate, existing, THRESHOLD, WINDOW_MS)
    assert match is None


def test_high_citation_overlap_within_window_flags() -> None:
    candidate = _fp("p2", cited=frozenset({"e1", "e2", "e3"}), created_at_ms=1_000_000)
    existing = (
        _fp("p1", cited=frozenset({"e1", "e2", "e3", "e4"}), created_at_ms=1_010_000),
    )
    match = find_duplicate(candidate, existing, THRESHOLD, WINDOW_MS)
    assert match is not None
    assert match.kind == "flag"
    assert match.other_id == "p1"


def test_citation_overlap_outside_window_does_not_flag() -> None:
    candidate = _fp("p2", cited=frozenset({"e1", "e2", "e3"}), created_at_ms=1_000_000)
    existing = (
        _fp(
            "p1",
            cited=frozenset({"e1", "e2", "e3", "e4"}),
            created_at_ms=1_000_000 + WINDOW_MS + 1,
        ),
    )
    match = find_duplicate(candidate, existing, THRESHOLD, WINDOW_MS)
    assert match is None


def test_low_citation_overlap_does_not_flag() -> None:
    candidate = _fp(
        "p2", cited=frozenset({"e1", "e2", "e3", "e4"}), created_at_ms=1_000_000
    )
    existing = (
        _fp("p1", cited=frozenset({"e4", "e5", "e6", "e7"}), created_at_ms=1_000_000),
    )
    match = find_duplicate(candidate, existing, THRESHOLD, WINDOW_MS)
    assert match is None


def test_no_false_positive_across_unrelated_conditions_and_disjoint_evidence() -> None:
    candidate = _fp(
        "p2",
        condition_name="test_failing",
        condition_params={"node_id": "tests/test_a.py::test_x"},
        cited=frozenset({"e1", "e2"}),
        created_at_ms=1_000_000,
    )
    existing = (
        _fp(
            "p1",
            condition_name="cpu_saturated",
            condition_params={"threshold": 0.9, "window_s": 60},
            cited=frozenset({"e9", "e10"}),
            created_at_ms=1_000_500,
        ),
    )
    match = find_duplicate(candidate, existing, THRESHOLD, WINDOW_MS)
    assert match is None


def test_both_empty_citation_sets_do_not_flag() -> None:
    candidate = _fp("p2", cited=frozenset(), created_at_ms=1_000_000)
    existing = (_fp("p1", cited=frozenset(), created_at_ms=1_000_000),)
    match = find_duplicate(candidate, existing, THRESHOLD, WINDOW_MS)
    assert match is None


def test_self_is_never_its_own_duplicate() -> None:
    candidate = _fp(
        "p1",
        condition_name="disk_free_below",
        condition_params={"mount": "/", "pct": 10},
        cited=frozenset({"e1"}),
        created_at_ms=1_000_000,
    )
    match = find_duplicate(candidate, (candidate,), THRESHOLD, WINDOW_MS)
    assert match is None
