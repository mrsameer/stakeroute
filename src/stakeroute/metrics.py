"""Five metrics, all computed from recorded run data (FR-033, FR-034).

None of these constants a value. Every function returns ``(value,
measured_over)`` where ``value`` is ``None`` exactly when there is not yet
enough recorded data to compute it — never a placeholder number. FR-034
prohibits displaying a fabricated or hard-coded benchmark figure, so
"not yet measured" must be representable, not approximated.
"""

from __future__ import annotations

from stakeroute.config import MIN_RESOLVED_FOR_CALIBRATION
from stakeroute.storage.repository import Repository


def measured_calibration(
    repo: Repository, tenant_id: str, agent_id: str
) -> tuple[float | None, int]:
    """An agent's own calibration, derived from its settlements alone —
    never a configured constant (FR-117). ``1 - mean Brier score``, so
    higher means better calibrated.

    Returns ``(None, resolved_count)`` below
    ``MIN_RESOLVED_FOR_CALIBRATION`` — real runs resolve far fewer
    outcomes than a seeded scenario, so "we do not know yet" must be
    representable rather than approximated by an early, noisy number
    (FR-118, D-022).
    """
    settlements = repo.list_settlements_for_agent(tenant_id, agent_id)
    resolved_count = len(settlements)
    if resolved_count < MIN_RESOLVED_FOR_CALIBRATION:
        return None, resolved_count
    mean_brier = sum(row["brier_score"] for row in settlements) / resolved_count
    return 1.0 - mean_brier, resolved_count


def precision_at_k(
    repo: Repository, tenant_id: str, strategy: str = "stakeroute"
) -> tuple[float | None, int]:
    """Fraction of routed hypotheses that resolved true, over the ones
    that have actually resolved (FR-033)."""
    decisions = [d for d in repo.latest_decisions(tenant_id, strategy) if d["routed"]]
    true_count = 0
    measured = 0
    for decision in decisions:
        outcome = repo.get_outcome(decision["hypothesis_id"])
        if outcome is None:
            continue
        measured += 1
        if outcome["outcome"] == 1:
            true_count += 1
    if measured == 0:
        return None, 0
    return true_count / measured, measured


def false_escalation_rate(
    repo: Repository, tenant_id: str, strategy: str = "stakeroute"
) -> tuple[float | None, int]:
    """Fraction of routed hypotheses that resolved false, over the ones
    that have actually resolved (FR-033)."""
    decisions = [d for d in repo.latest_decisions(tenant_id, strategy) if d["routed"]]
    false_count = 0
    measured = 0
    for decision in decisions:
        outcome = repo.get_outcome(decision["hypothesis_id"])
        if outcome is None:
            continue
        measured += 1
        if outcome["outcome"] == 0:
            false_count += 1
    if measured == 0:
        return None, 0
    return false_count / measured, measured


def time_to_attention_ms(
    repo: Repository, tenant_id: str, strategy: str = "stakeroute"
) -> tuple[float | None, int]:
    """Mean ``decided_at_ms - created_at_ms`` for routed hypotheses that
    resolved true (FR-033)."""
    decisions = [d for d in repo.latest_decisions(tenant_id, strategy) if d["routed"]]
    deltas: list[int] = []
    for decision in decisions:
        outcome = repo.get_outcome(decision["hypothesis_id"])
        if outcome is None or outcome["outcome"] != 1:
            continue
        hypothesis = repo.get_hypothesis(decision["hypothesis_id"])
        if hypothesis is None:
            continue
        deltas.append(decision["decided_at_ms"] - hypothesis["created_at_ms"])
    if not deltas:
        return None, 0
    return sum(deltas) / len(deltas), len(deltas)


def mean_brier_score(repo: Repository, tenant_id: str) -> tuple[float | None, int]:
    """Mean Brier score over every settled forecast (FR-033)."""
    settlements = repo.list_all_settlements(tenant_id)
    if not settlements:
        return None, 0
    total = sum(row["brier_score"] for row in settlements)
    return total / len(settlements), len(settlements)


def events_per_second(repo: Repository, tenant_id: str) -> tuple[float | None, int]:
    """Events ingested per second, measured over the recorded ingestion span.

    Duration is floored at 1ms — a synchronous, in-process ingestion run
    can legitimately have every event land within the same millisecond,
    and reporting the true (near-zero) elapsed time would divide by zero
    rather than produce a meaningful rate. The floor makes the reported
    number a conservative lower bound on throughput, not an inflated one.
    """
    span = repo.event_ingestion_span_ms(tenant_id)
    if span is None:
        return None, 0
    count, earliest_ms, latest_ms = span
    duration_s = max((latest_ms - earliest_ms) / 1000.0, 0.001)
    return count / duration_s, count


def ranking_pass_lag_ms(
    repo: Repository, tenant_id: str, strategy: str = "stakeroute"
) -> tuple[float | None, int]:
    """How far the newest ranking decision trails the newest ingested event.

    Not one of the five headline metrics, but the measurement SC-011's
    throughput claim rests on: the pipeline must not let the operator
    queue fall behind the event stream.
    """
    newest_event_ms = repo.newest_event_ingested_at_ms(tenant_id)
    newest_decision_ms = repo.newest_decision_ms(tenant_id, strategy)
    if newest_event_ms is None or newest_decision_ms is None:
        return None, 0
    lag = max(newest_decision_ms - newest_event_ms, 0)
    return float(lag), 1
