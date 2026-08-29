"""Deterministic near-duplicate detection (FR-110, D-023).

A rule over bound conditions and cited observation sets — derivable on a
whiteboard, not an embedding similarity score no one can derive under
questioning (Principle II). ``ATTENTION_BUDGET = 2`` makes this matter more
than its size suggests: one duplicated incident consuming two of two
review slots is a total queue failure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProposalFingerprint:
    """The slice of a proposal duplicate detection needs to compare."""

    id: str
    condition_name: str | None
    condition_params: Mapping[str, object] | None
    cited_observation_ids: frozenset[str]
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    """A detected duplicate: ``'merge'`` on an exact condition match,
    ``'flag'`` for the operator otherwise."""

    other_id: str
    kind: str  # 'merge' | 'flag'
    basis: str


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_duplicate(
    candidate: ProposalFingerprint,
    existing: tuple[ProposalFingerprint, ...],
    jaccard_threshold: float,
    window_ms: int,
) -> DuplicateMatch | None:
    """Return the first duplicate ``candidate`` matches against, or
    ``None``.

    Exact condition-name-and-parameters matches take priority and merge
    (D-023); citation-overlap matches within ``window_ms`` flag for the
    operator instead. A candidate is never its own duplicate.
    """
    others = tuple(o for o in existing if o.id != candidate.id)

    if candidate.condition_name is not None:
        for other in others:
            if (
                other.condition_name == candidate.condition_name
                and other.condition_params == candidate.condition_params
            ):
                return DuplicateMatch(
                    other_id=other.id,
                    kind="merge",
                    basis="same bound condition and parameters",
                )

    for other in others:
        if abs(candidate.created_at_ms - other.created_at_ms) > window_ms:
            continue
        overlap = _jaccard(candidate.cited_observation_ids, other.cited_observation_ids)
        if overlap >= jaccard_threshold:
            return DuplicateMatch(
                other_id=other.id,
                kind="flag",
                basis=(
                    f"citation overlap jaccard={overlap:.2f} "
                    f"within {window_ms}ms window"
                ),
            )

    return None
