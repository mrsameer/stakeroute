"""Tests for the enforced evidence exclusion (FR-113, SC-102, D-013).

FR-113's exclusion is enforced by the type, not by a convention — these
tests walk the dataclass fields rather than exercising behaviour, because
the claim being tested is exactly "no such field exists to begin with".
"""

from __future__ import annotations

import dataclasses

from stakeroute.core.types import ObservationSnapshot
from stakeroute.real.scopes import EvidenceAccessScope, EvidenceBundle

_BANNED_TOKENS = ("outcome", "ground_truth", "groundtruth", "accuracy", "resolved")


def _field_names(cls: type) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def test_evidence_bundle_is_frozen() -> None:
    params = dataclasses.fields(EvidenceBundle)
    assert params  # sanity: it has fields at all
    scope = EvidenceAccessScope(
        agent_id="a1", source_ids=frozenset({"host.metrics"}), label="host"
    )
    bundle = EvidenceBundle(
        agent_id="a1",
        hypothesis_id="h1",
        hypothesis_statement="stmt",
        scope=scope,
        observations=(),
        built_at_ms=0,
    )
    try:
        bundle.agent_id = "a2"  # type: ignore[misc]
        raised = False
    except dataclasses.FrozenInstanceError:
        raised = True
    assert raised


def test_evidence_bundle_fields_carry_no_outcome_or_accuracy() -> None:
    names = _field_names(EvidenceBundle)
    for token in _BANNED_TOKENS:
        assert not any(token in name.lower() for name in names), (
            f"EvidenceBundle has a field resembling {token!r}: {names}"
        )


def test_observation_snapshot_fields_carry_no_outcome_or_accuracy() -> None:
    names = _field_names(ObservationSnapshot)
    for token in _BANNED_TOKENS:
        assert not any(token in name.lower() for name in names), (
            f"ObservationSnapshot has a field resembling {token!r}: {names}"
        )


def test_evidence_access_scope_fields_carry_no_outcome_or_accuracy() -> None:
    names = _field_names(EvidenceAccessScope)
    for token in _BANNED_TOKENS:
        assert not any(token in name.lower() for name in names), (
            f"EvidenceAccessScope has a field resembling {token!r}: {names}"
        )


def test_evidence_bundle_expected_fields_present() -> None:
    assert _field_names(EvidenceBundle) == {
        "agent_id",
        "hypothesis_id",
        "hypothesis_statement",
        "scope",
        "observations",
        "built_at_ms",
    }
