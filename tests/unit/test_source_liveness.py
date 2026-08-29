"""Tests for the source liveness state machine (FR-141, contracts/observations.md).

A silent source and a quiet one must never render the same — this is the
whole of what these tests hold the mechanism to.
"""

from __future__ import annotations

from stakeroute.real.collectors import next_liveness_state

THRESHOLD_MS = 30_000


def test_live_stays_live_when_observed() -> None:
    assert next_liveness_state("live", True, 0, THRESHOLD_MS) == "live"


def test_live_moves_to_quiet_after_one_empty_poll() -> None:
    # Live -> quiet immediately on the first empty poll, regardless of how
    # little time has elapsed since the last observation.
    assert next_liveness_state("live", False, 1, THRESHOLD_MS) == "quiet"


def test_quiet_stays_quiet_below_the_silence_threshold() -> None:
    assert (
        next_liveness_state("quiet", False, THRESHOLD_MS - 1, THRESHOLD_MS) == "quiet"
    )


def test_quiet_moves_to_silent_at_the_threshold() -> None:
    assert next_liveness_state("quiet", False, THRESHOLD_MS, THRESHOLD_MS) == "silent"


def test_quiet_moves_to_silent_beyond_the_threshold() -> None:
    assert (
        next_liveness_state("quiet", False, THRESHOLD_MS + 5_000, THRESHOLD_MS)
        == "silent"
    )


def test_silent_stays_silent_while_still_unobserved() -> None:
    assert (
        next_liveness_state("silent", False, THRESHOLD_MS * 3, THRESHOLD_MS) == "silent"
    )


def test_any_observation_returns_a_quiet_source_to_live() -> None:
    assert next_liveness_state("quiet", True, THRESHOLD_MS + 1, THRESHOLD_MS) == "live"


def test_any_observation_returns_a_silent_source_to_live() -> None:
    assert next_liveness_state("silent", True, THRESHOLD_MS * 5, THRESHOLD_MS) == "live"


def test_absent_is_terminal_even_when_observed() -> None:
    # A source flagged absent at startup does not resurrect mid-run — the
    # binary/runtime it depends on does not appear part-way through.
    assert next_liveness_state("absent", True, 0, THRESHOLD_MS) == "absent"


def test_absent_is_terminal_regardless_of_elapsed_time() -> None:
    assert next_liveness_state("absent", False, 10**9, THRESHOLD_MS) == "absent"


def test_silent_and_quiet_are_never_the_same_value() -> None:
    quiet = next_liveness_state("quiet", False, THRESHOLD_MS - 1, THRESHOLD_MS)
    silent = next_liveness_state("quiet", False, THRESHOLD_MS, THRESHOLD_MS)
    assert quiet != silent
