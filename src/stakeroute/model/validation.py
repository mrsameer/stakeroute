"""Proposal and forecast response validation (FR-122, contracts/model-boundary.md).

**Note what is absent from the proposal schema**: there is no ``impact``,
``urgency``, ``review_cost`` or ``probability`` field. Those are computed by
``core/estimates.py`` from the cited observations (D-016) — the schema
itself is the enforcement, since the model is never asked for a number.

The checkable-condition parameter signatures are read from
``real/conditions.py``'s closed registry — the same source that runs the
check later, so a name or parameter set accepted here is guaranteed
runnable there (D-017).
"""

from __future__ import annotations

from stakeroute.config import STAKE_MAX, STAKE_MIN
from stakeroute.model.protocol import ForecastDraft, ProposalDraft, RejectionReason
from stakeroute.real.conditions import CONDITIONS

STATEMENT_MAX_LEN = 500


def validate_proposal(
    raw: dict,
    known_observation_ids: set[str],
    observation_ts_by_id: dict[str, int],
    window_start_ms: int,
    window_end_ms: int,
) -> ProposalDraft | RejectionReason:
    """Validate a ``purpose='proposal'`` response against every rule in
    contracts/model-boundary.md, in the order that makes each check's
    input well-defined."""
    if not isinstance(raw, dict):
        return "MALFORMED_SHAPE"

    if "refusal" in raw and "statement" not in raw:
        return "REFUSAL"

    statement = raw.get("statement")
    if (
        not isinstance(statement, str)
        or not statement
        or len(statement) > STATEMENT_MAX_LEN
    ):
        return "MALFORMED_SHAPE"

    cited_raw = raw.get("cited_observation_ids")
    if not isinstance(cited_raw, list) or not all(
        isinstance(i, str) for i in cited_raw
    ):
        return "MALFORMED_SHAPE"
    if not cited_raw:
        return "NO_CITATIONS"

    unknown = [i for i in cited_raw if i not in known_observation_ids]
    if unknown:
        return "UNKNOWN_CITATION"

    out_of_window = [
        i
        for i in cited_raw
        if not (window_start_ms <= observation_ts_by_id[i] <= window_end_ms)
    ]
    if out_of_window:
        return "CITATION_OUT_OF_WINDOW"

    condition_name = raw.get("condition_name")
    condition_params = raw.get("condition_params")
    if condition_name is not None:
        if not isinstance(condition_name, str):
            return "MALFORMED_SHAPE"
        spec = CONDITIONS.get(condition_name)
        if spec is None:
            return "UNKNOWN_CONDITION"
        if not isinstance(condition_params, dict):
            return "INVALID_CONDITION_PARAMS"
        if frozenset(condition_params.keys()) != spec.param_names:
            return "INVALID_CONDITION_PARAMS"
    elif condition_params is not None:
        return "MALFORMED_SHAPE"

    return ProposalDraft(
        statement=statement,
        cited_observation_ids=tuple(cited_raw),
        condition_name=condition_name,
        condition_params=condition_params,
    )


def validate_forecast(
    raw: dict,
    in_scope_sources: frozenset[str],
    all_source_ids: frozenset[str],
    available_credits: int,
) -> ForecastDraft | RejectionReason:
    """Validate a ``purpose='forecast'`` response against every rule in
    contracts/model-boundary.md."""
    if not isinstance(raw, dict):
        return "MALFORMED_SHAPE"

    if "refusal" in raw and "probability" not in raw:
        return "REFUSAL"

    probability = raw.get("probability")
    stake = raw.get("stake")
    rationale = raw.get("rationale")
    if (
        not isinstance(probability, (int, float))
        or isinstance(probability, bool)
        or not isinstance(stake, int)
        or isinstance(stake, bool)
        or not isinstance(rationale, str)
    ):
        return "MALFORMED_SHAPE"

    if not (0.0 <= probability <= 1.0):
        return "PROBABILITY_OUT_OF_RANGE"

    if not (STAKE_MIN <= stake <= STAKE_MAX):
        return "STAKE_OUT_OF_RANGE"

    if stake > available_credits:
        return "INSUFFICIENT_CREDITS"

    if not rationale:
        return "MALFORMED_SHAPE"

    out_of_scope_sources = all_source_ids - in_scope_sources
    if any(source in rationale for source in out_of_scope_sources):
        return "EVIDENCE_SCOPE_VIOLATION"

    return ForecastDraft(
        probability=float(probability), stake=stake, rationale=rationale
    )
