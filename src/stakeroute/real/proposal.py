"""Hypothesis proposal from observations (FR-107, FR-111, D-016).

The model supplies exactly three things — a statement, citations, and an
optional checkable condition. Every number that reaches ``priority_score``
is computed by ``core/estimates.py`` from the cited observations, wired in
here — never read from the model's response (the proposal schema has no
such field at all, see ``model/validation.py``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from stakeroute.config import DUPLICATE_JACCARD_THRESHOLD, DUPLICATE_WINDOW_MS
from stakeroute.core.duplicates import ProposalFingerprint, find_duplicate
from stakeroute.core.estimates import (
    estimate_impact,
    estimate_review_cost,
    estimate_urgency,
)
from stakeroute.core.types import ObservationSnapshot
from stakeroute.model.budget import rejection_for_state
from stakeroute.model.protocol import ProposalDraft, Rejected, RejectionReason
from stakeroute.model.recorder import ModelInteractionRecorder
from stakeroute.model.validation import validate_proposal
from stakeroute.storage.repository import Repository

DEFAULT_HYPOTHESIS_PRIOR = 0.5


@dataclass(frozen=True, slots=True)
class ProposalCycleResult:
    """What one proposal attempt produced."""

    status: str  # 'promoted' | 'merged' | 'rejected' | 'no_observations'
    hypothesis_id: str | None = None
    proposal_id: str | None = None
    rejection_reason: RejectionReason | None = None
    duplicate_of: str | None = None


def build_proposal_prompt(observations: tuple[ObservationSnapshot, ...]) -> str:
    """Every field here is already redacted — it was redacted at the
    ingestion boundary before it ever reached ``events`` (D-014)."""
    lines = ["Observations (id | source | observed_at_ms | payload):"]
    for obs in observations:
        lines.append(
            f"- {obs.event_id} | {obs.source} | {obs.observed_at_ms} | {obs.payload}"
        )
    lines.append(
        "\nPropose one hypothesis about what these observations indicate. "
        "Cite only observation ids from the list above. Optionally bind a "
        "checkable condition from the registry with matching parameters, or "
        "leave it null. Respond as JSON with exactly these fields: "
        '{"statement": str, "cited_observation_ids": [str, ...], '
        '"condition_name": str | null, "condition_params": object | null}'
    )
    return "\n".join(lines)


def _existing_fingerprints(
    repo: Repository, tenant_id: str
) -> tuple[ProposalFingerprint, ...]:
    fingerprints = []
    for row in repo.list_proposals(tenant_id):
        if row["status"] not in ("pending", "promoted"):
            continue
        fingerprints.append(
            ProposalFingerprint(
                id=row["id"],
                condition_name=row["condition_name"],
                condition_params=(
                    json.loads(row["condition_params"])
                    if row["condition_params"]
                    else None
                ),
                cited_observation_ids=frozenset(
                    json.loads(row["cited_observation_ids"])
                ),
                created_at_ms=row["created_at_ms"],
            )
        )
    return tuple(fingerprints)


async def run_proposal_cycle(
    repo: Repository,
    tenant_id: str,
    model: ModelInteractionRecorder,
    observations: tuple[ObservationSnapshot, ...],
    window_start_ms: int,
    window_end_ms: int,
    now_ms: int,
    timeout_s: float,
    deadline_ms: int | None = None,
) -> ProposalCycleResult:
    """Propose a hypothesis from ``observations``, validate it, and either
    promote it to a hypothesis or merge it into an existing one.

    Nothing enters the ranked queue before it is durably recorded: the
    ``proposals`` row is committed before the ``hypotheses`` row that
    follows it (FR-111).
    """
    if not observations:
        return ProposalCycleResult(status="no_observations")

    # Check the model's own reported capability before building a prompt
    # for it — a ceiling-exhausted or unconfigured model degrades this
    # capability, reported plainly and still recorded, rather than being
    # attempted and failing inside the call (D-020).
    precheck_reason = rejection_for_state(model.state().state)
    if precheck_reason is not None:
        rejected = model.record_precheck_rejection("proposal", precheck_reason)
        return ProposalCycleResult(status="rejected", rejection_reason=rejected.reason)

    known_ids = {o.event_id for o in observations}
    ts_by_id = {o.event_id: o.observed_at_ms for o in observations}
    prompt = build_proposal_prompt(observations)

    def _validate(raw: dict) -> ProposalDraft | RejectionReason:
        return validate_proposal(
            raw, known_ids, ts_by_id, window_start_ms, window_end_ms
        )

    result = await model.complete(
        purpose="proposal", prompt=prompt, timeout_s=timeout_s, validate=_validate
    )

    if isinstance(result, Rejected):
        return ProposalCycleResult(status="rejected", rejection_reason=result.reason)

    draft = result.value
    proposal_id = f"proposal-{result.interaction_id}"

    candidate_fp = ProposalFingerprint(
        id=proposal_id,
        condition_name=draft.condition_name,
        condition_params=draft.condition_params,
        cited_observation_ids=frozenset(draft.cited_observation_ids),
        created_at_ms=now_ms,
    )
    match = find_duplicate(
        candidate_fp,
        _existing_fingerprints(repo, tenant_id),
        DUPLICATE_JACCARD_THRESHOLD,
        DUPLICATE_WINDOW_MS,
    )
    merge_target = (
        match.other_id if match is not None and match.kind == "merge" else None
    )

    repo.insert_proposal(
        proposal_id=proposal_id,
        tenant_id=tenant_id,
        mode="real",
        statement=draft.statement,
        cited_observation_ids=list(draft.cited_observation_ids),
        condition_name=draft.condition_name,
        condition_params=draft.condition_params,
        interaction_id=result.interaction_id,
        status="merged" if merge_target is not None else "pending",
        created_at_ms=now_ms,
        merged_into=merge_target,
    )
    repo.commit()

    if merge_target is not None:
        return ProposalCycleResult(
            status="merged", proposal_id=proposal_id, duplicate_of=merge_target
        )

    cited_snapshots = tuple(
        o for o in observations if o.event_id in draft.cited_observation_ids
    )
    impact = estimate_impact(cited_snapshots)
    urgency = estimate_urgency(cited_snapshots, now_ms)
    review_cost = estimate_review_cost(cited_snapshots)

    hypothesis_id = f"h-{proposal_id}"
    resolved_deadline_ms = (
        deadline_ms if deadline_ms is not None else now_ms + 60 * 60 * 1000
    )
    repo.upsert_hypothesis(
        hypothesis_id=hypothesis_id,
        tenant_id=tenant_id,
        statement=draft.statement,
        prior_probability=DEFAULT_HYPOTHESIS_PRIOR,
        impact_minor_units=round(impact.value),
        urgency=urgency.value,
        review_cost=review_cost.value,
        deadline_ms=resolved_deadline_ms,
        status="open",
        created_at_ms=now_ms,
        mode="real",
        proposal_id=proposal_id,
        condition_name=draft.condition_name,
        condition_params=draft.condition_params,
    )
    for estimate in (impact, urgency, review_cost):
        repo.insert_attribute_estimate(
            tenant_id=tenant_id,
            hypothesis_id=hypothesis_id,
            attribute=estimate.attribute,
            value=estimate.value,
            basis=estimate.basis,
            estimator=estimate.estimator,
            confirmed_by_operator=False,
            confirmed_at_ms=None,
            created_at_ms=now_ms,
        )
    repo.set_proposal_status(proposal_id, "promoted")
    repo.commit()

    return ProposalCycleResult(
        status="promoted",
        hypothesis_id=hypothesis_id,
        proposal_id=proposal_id,
        duplicate_of=match.other_id if match is not None else None,
    )
