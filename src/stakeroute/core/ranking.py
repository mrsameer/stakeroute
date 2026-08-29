"""Priority scoring and budgeted attention allocation (FR-019 through FR-022)."""

from __future__ import annotations

from stakeroute.core.types import (
    AllocationDecision,
    AllocationResult,
    RankedHypothesis,
)


def priority_score(
    probability: float, impact_minor_units: int, urgency: float, review_cost: float
) -> float:
    """Return ``probability * impact * urgency / review_cost``.

    Raises:
        ValueError: if ``review_cost`` is not strictly positive.
    """
    if review_cost <= 0:
        raise ValueError(f"review_cost must be > 0, got {review_cost!r}")
    return probability * impact_minor_units * urgency / review_cost


def allocate_attention(
    ranked: tuple[RankedHypothesis, ...], budget: int
) -> AllocationResult:
    """Allocate a fixed human-review budget across ranked hypotheses.

    Never returns more than ``budget`` routed entries (Constitution
    Principle IV). Ties break on ``(-priority, -impact, hypothesis_id)`` so
    ordering is deterministic regardless of input order. Withheld
    hypotheses are recorded, never silently dropped.
    """
    ordered = sorted(
        ranked,
        key=lambda h: (-h.priority, -h.impact_minor_units, h.hypothesis_id),
    )

    decisions: list[AllocationDecision] = []
    withheld = 0
    for rank, hypothesis in enumerate(ordered, start=1):
        routed = rank <= budget
        if routed:
            reason = f"rank {rank} of {len(ordered)}; within budget of {budget}"
        else:
            reason = (
                f"rank {rank} of {len(ordered)}; withheld — budget of "
                f"{budget} exhausted"
            )
            withheld += 1
        decisions.append(
            AllocationDecision(
                hypothesis_id=hypothesis.hypothesis_id,
                rank=rank,
                routed=routed,
                reason=reason,
            )
        )

    return AllocationResult(decisions=tuple(decisions), withheld_count=withheld)
