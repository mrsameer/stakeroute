"""Settlement against ground truth, and expiry handling (User Story 3).

Both operations run inside a single transaction each: settling a
hypothesis scores every live forecast against the outcome, updates
reputations, releases stakes, and marks the hypothesis resolved; expiring
one returns stakes in full with reputation untouched and no outcome
inferred (FR-028). Neither imports from ``stakeroute.core`` directly for
I/O — they call the pure functions there and apply the results.
"""

from __future__ import annotations

from stakeroute.core.reputation import update_reputation
from stakeroute.core.settlement import settle_forecast
from stakeroute.storage.repository import Repository


def settle_hypothesis(
    repo: Repository,
    tenant_id: str,
    hypothesis_id: str,
    outcome: int,
    resolved_by: str,
    resolved_at_ms: int,
) -> bool:
    """Settle every live forecast on ``hypothesis_id`` against ``outcome``.

    Returns ``False`` (a no-op) if the outcome was already recorded for
    this hypothesis — the ``outcomes`` half of the idempotency guarantee
    alongside ``insert_event`` (FR-003, SC-005). Returns ``True`` if
    settlement actually ran.
    """
    is_new_outcome = repo.insert_outcome(
        hypothesis_id, tenant_id, outcome, resolved_at_ms, resolved_by
    )
    if not is_new_outcome:
        return False

    hypothesis = repo.get_hypothesis(hypothesis_id)
    if hypothesis is None:
        repo.commit()
        return False

    for forecast_row in repo.list_forecasts_for_hypothesis(hypothesis_id):
        # Settle exactly the forecasts open when the outcome arrived
        # (FR-134): created no later than, and not yet expired at,
        # resolved_at_ms. A no-op filter for feature 001 — its forecasts'
        # expires_at_ms is always far beyond any resolution it settles.
        if forecast_row["created_at_ms"] > resolved_at_ms:
            continue
        if forecast_row["expires_at_ms"] <= resolved_at_ms:
            continue

        agent = repo.get_agent(forecast_row["agent_id"])
        if agent is None:
            continue

        settlement = settle_forecast(
            forecast_id=forecast_row["id"],
            stake=forecast_row["stake"],
            prior_probability=hypothesis["prior_probability"],
            probability=forecast_row["probability"],
            outcome=outcome,
        )
        reputation_before = agent["reputation"]
        reputation_after = update_reputation(
            current=reputation_before,
            brier_score=settlement.brier_score,
            improvement=settlement.improvement,
        )

        inserted = repo.insert_settlement(
            tenant_id=tenant_id,
            forecast_id=forecast_row["id"],
            brier_score=settlement.brier_score,
            prior_brier_score=settlement.prior_brier_score,
            improvement=settlement.improvement,
            credit_delta=settlement.credit_delta,
            reputation_before=reputation_before,
            reputation_after=reputation_after,
            settled_at_ms=resolved_at_ms,
        )
        if not inserted:
            continue  # UNIQUE(forecast_id) already settled — no-op (SC-005)

        repo.set_agent_reputation(agent["id"], reputation_after)
        # Release the stake and apply the settlement delta in the same
        # update: staked credits for this forecast return to zero, and
        # available credits move by the stake plus the signed delta (a
        # loss subtracts from what comes back, never below zero net).
        repo.adjust_agent_credits(
            agent["id"],
            available_delta=forecast_row["stake"] + settlement.credit_delta,
            staked_delta=-forecast_row["stake"],
        )

    repo.set_hypothesis_status(hypothesis_id, "resolved")
    repo.commit()
    return True


def expire_overdue_hypotheses(
    repo: Repository, tenant_id: str, now_ms: int
) -> list[str]:
    """Expire every open hypothesis past its deadline (FR-028).

    Stakes are returned in full, reputation is left untouched, and no
    outcome is inferred — this is a deliberate no-signal resolution, not a
    settlement. Returns the ids of hypotheses expired by this call.
    """
    expired_ids: list[str] = []
    for hypothesis in repo.list_expired_hypotheses(tenant_id, now_ms):
        for forecast_row in repo.list_forecasts_for_hypothesis(hypothesis["id"]):
            repo.adjust_agent_credits(
                forecast_row["agent_id"],
                available_delta=forecast_row["stake"],
                staked_delta=-forecast_row["stake"],
            )
        repo.set_hypothesis_status(hypothesis["id"], "expired")
        expired_ids.append(hypothesis["id"])
    repo.commit()
    return expired_ids
