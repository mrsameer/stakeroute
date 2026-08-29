"""Pure attribute estimators: impact, urgency, review cost (FR-108, D-016).

**This is the module that makes FR-108 and FR-119 both hold.** A model
proposal supplies a statement and citations; every number that reaches
``priority_score`` is computed here, from the cited observations alone.
Nothing in this file imports ``stakeroute.model`` — the purity test (T025)
enforces that mechanically, not by convention.
"""

from __future__ import annotations

from stakeroute.core.types import AttributeEstimate, ObservationSnapshot

# Deliberately crude constants (D-016's stated cost): a rule over counts and
# severities, not a judgement. The basis field carries the derivation so an
# operator can see exactly how a number was reached.
IMPACT_UNIT_PER_OBSERVATION = 50_000
URGENCY_HORIZON_MS = 24 * 60 * 60 * 1000  # one day
URGENCY_FLOOR = 0.05
REVIEW_COST_PER_SOURCE = 1.0


def estimate_impact(
    observations: tuple[ObservationSnapshot, ...],
) -> AttributeEstimate:
    """Impact grows with how many observations corroborate the situation
    and how severe they are — more corroborating evidence and worse
    readings mean a larger candidate business impact.

    Raises:
        ValueError: if ``observations`` is empty — an estimate needs
            something to be estimated from.
    """
    if not observations:
        raise ValueError("estimate_impact requires at least one observation")

    count = len(observations)
    mean_severity = sum(o.severity for o in observations) / count
    value = IMPACT_UNIT_PER_OBSERVATION * count * mean_severity
    sources = sorted({o.source for o in observations})
    basis = (
        f"{count} observation(s) across {len(sources)} source(s) "
        f"({', '.join(sources)}), mean severity {mean_severity:.2f}; "
        f"estimator=estimate_impact"
    )
    return AttributeEstimate(
        attribute="impact", value=value, basis=basis, estimator="estimate_impact"
    )


def estimate_urgency(
    observations: tuple[ObservationSnapshot, ...], now_ms: int
) -> AttributeEstimate:
    """Urgency is higher the more recently the situation was last observed,
    decaying linearly to a floor over ``URGENCY_HORIZON_MS``.

    ``now_ms`` is an explicit argument, not a clock read — the core still
    reads no clock (Principle I).

    Raises:
        ValueError: if ``observations`` is empty.
    """
    if not observations:
        raise ValueError("estimate_urgency requires at least one observation")

    most_recent_ms = max(o.observed_at_ms for o in observations)
    age_ms = max(now_ms - most_recent_ms, 0)
    fraction_elapsed = min(age_ms / URGENCY_HORIZON_MS, 1.0)
    value = max(URGENCY_FLOOR, 1.0 - fraction_elapsed)
    value = min(value, 1.0)
    basis = (
        f"most recent observation {age_ms}ms ago, "
        f"{URGENCY_HORIZON_MS}ms horizon; estimator=estimate_urgency"
    )
    return AttributeEstimate(
        attribute="urgency", value=value, basis=basis, estimator="estimate_urgency"
    )


def estimate_review_cost(
    observations: tuple[ObservationSnapshot, ...],
) -> AttributeEstimate:
    """Review cost grows with the breadth of scope — how many distinct
    sources an operator would need to look at to review the hypothesis.

    Raises:
        ValueError: if ``observations`` is empty.
    """
    if not observations:
        raise ValueError("estimate_review_cost requires at least one observation")

    sources = sorted({o.source for o in observations})
    value = REVIEW_COST_PER_SOURCE * len(sources)
    scope = "single-source" if len(sources) == 1 else f"{len(sources)}-source"
    basis = f"{scope} scope ({', '.join(sources)}); estimator=estimate_review_cost"
    return AttributeEstimate(
        attribute="review_cost",
        value=value,
        basis=basis,
        estimator="estimate_review_cost",
    )
