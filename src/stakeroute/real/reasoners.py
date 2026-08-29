"""Reasoning agents that hold a bundle and nothing else (D-013).

``forecast`` is the enforced signature: no ``Repository`` handle, no
tenant id, no outcome. An agent that has no way to reach the outcome
cannot leak it — a reviewer can check that claim by reading one function
signature rather than auditing a prompt.

``run_agent_forecast`` is the orchestration wrapper that *does* hold a
repo — it calls ``forecast``, then persists either a ``forecasts`` row or
a ``rejected_forecasts`` row (FR-116), reusing feature 001's stake
reconciliation exactly as ``worker/main.py::handle_forecast_created``
does for simulated forecasts.
"""

from __future__ import annotations

from dataclasses import dataclass

from stakeroute.config import ALL_EVIDENCE_SOURCE_IDS
from stakeroute.core.types import clamp_probability
from stakeroute.model.budget import rejection_for_state
from stakeroute.model.protocol import ForecastDraft, Rejected, RejectionReason
from stakeroute.model.recorder import ModelInteractionRecorder
from stakeroute.model.validation import validate_forecast
from stakeroute.real.scopes import (
    EvidenceBundle,
    ForecastProposal,
    serialize_evidence_bundle,
)
from stakeroute.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class ForecastRejection:
    """A rejected forecast attempt — recorded, never silently dropped."""

    reason: RejectionReason
    interaction_id: str


def build_forecast_prompt(bundle: EvidenceBundle) -> str:
    """Every field here is already redacted (D-014) — the bundle is built
    from ``events.payload``, which never holds unredacted content."""
    scope_sources = sorted(bundle.scope.source_ids)
    lines = [
        f"Hypothesis: {bundle.hypothesis_statement}",
        f"Your evidence scope: {bundle.scope.label} ({scope_sources})",
        "Observations you can see (id | source | observed_at_ms | payload):",
    ]
    for obs in bundle.observations:
        lines.append(
            f"- {obs.event_id} | {obs.source} | {obs.observed_at_ms} | {obs.payload}"
        )
    lines.append(
        "\nForecast the probability this hypothesis is true, based only on "
        "the observations above. Respond as JSON with exactly these "
        'fields: {"probability": float in [0,1], "stake": int, '
        '"rationale": str}. Cite only evidence from your own scope in the '
        "rationale."
    )
    return "\n".join(lines)


async def forecast(
    bundle: EvidenceBundle,
    model: ModelInteractionRecorder,
    available_credits: int,
    timeout_s: float,
) -> ForecastProposal | ForecastRejection:
    """Forecast over ``bundle`` alone. Holds no ``Repository`` handle, no
    tenant id and no outcome (D-013) — ``available_credits`` is the one
    piece of economic state it needs, handed in as a plain integer so
    stake rationing can be enforced (US1 acceptance scenario 5) without
    granting database access.
    """
    # Check the model's own reported capability before building a prompt
    # for it — a ceiling-exhausted or unconfigured model degrades this
    # capability, reported plainly and still recorded (D-020).
    precheck_reason = rejection_for_state(model.state().state)
    if precheck_reason is not None:
        rejected = model.record_precheck_rejection("forecast", precheck_reason)
        return ForecastRejection(
            reason=rejected.reason, interaction_id=rejected.interaction_id
        )

    prompt = build_forecast_prompt(bundle)
    in_scope_sources = bundle.scope.source_ids

    def _validate(raw: dict) -> ForecastDraft | RejectionReason:
        return validate_forecast(
            raw, in_scope_sources, ALL_EVIDENCE_SOURCE_IDS, available_credits
        )

    result = await model.complete(
        purpose="forecast",
        prompt=prompt,
        timeout_s=timeout_s,
        validate=_validate,
        agent_id=bundle.agent_id,
    )

    if isinstance(result, Rejected):
        return ForecastRejection(
            reason=result.reason, interaction_id=result.interaction_id
        )

    draft = result.value
    evidence_cluster_id = ",".join(sorted(bundle.scope.source_ids))
    return ForecastProposal(
        agent_id=bundle.agent_id,
        hypothesis_id=bundle.hypothesis_id,
        probability=draft.probability,
        stake=draft.stake,
        evidence_cluster_id=evidence_cluster_id,
        rationale=draft.rationale,
        interaction_id=result.interaction_id,
    )


async def run_agent_forecast(
    repo: Repository,
    tenant_id: str,
    bundle: EvidenceBundle,
    model: ModelInteractionRecorder,
    now_ms: int,
    timeout_s: float,
    expires_at_ms: int,
) -> ForecastProposal | None:
    """Call ``forecast`` and persist its result — the one place in real
    mode with both a bundle and a repository handle.

    Returns the accepted ``ForecastProposal``, or ``None`` if it was
    rejected (the rejection is still durably recorded either way — in
    ``model_interactions`` always, and in ``rejected_forecasts`` for every
    reason FR-116 names).
    """
    agent = repo.get_agent(bundle.agent_id)
    available_credits = agent["available_credits"] if agent is not None else 0

    result = await forecast(bundle, model, available_credits, timeout_s)

    if isinstance(result, ForecastRejection):
        repo.insert_rejected_forecast(
            tenant_id=tenant_id,
            hypothesis_id=bundle.hypothesis_id,
            agent_id=bundle.agent_id,
            stake=0,
            probability=0.0,
            reason=result.reason,
            rejected_at_ms=now_ms,
        )
        repo.commit()
        return None

    probability = clamp_probability(result.probability)
    repo.ensure_evidence_cluster(
        result.evidence_cluster_id, tenant_id, result.evidence_cluster_id
    )
    forecast_id = f"forecast-{bundle.hypothesis_id}-{bundle.agent_id}"
    previous = repo.upsert_forecast(
        forecast_id=forecast_id,
        tenant_id=tenant_id,
        hypothesis_id=bundle.hypothesis_id,
        agent_id=bundle.agent_id,
        probability=probability,
        stake=result.stake,
        evidence_cluster_id=result.evidence_cluster_id,
        evidence_refs=[o.event_id for o in bundle.observations],
        source_event_id=result.interaction_id,
        created_at_ms=now_ms,
        expires_at_ms=expires_at_ms,
        mode="real",
        evidence_bundle=serialize_evidence_bundle(bundle),
        rationale=result.rationale,
        interaction_id=result.interaction_id,
    )
    if previous is not None:
        _, previous_stake = previous.split(":")
        repo.adjust_agent_credits(
            bundle.agent_id,
            available_delta=int(previous_stake),
            staked_delta=-int(previous_stake),
        )
    repo.adjust_agent_credits(
        bundle.agent_id, available_delta=-result.stake, staked_delta=result.stake
    )
    repo.commit()
    return result
