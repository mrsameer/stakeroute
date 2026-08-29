"""Price a rank-1 attack against the run that actually happened.

``stakeroute.core.attack`` holds the closed form. This module supplies it
with real numbers: the honest influence weight, aggregate probability, vote
tally, and hypothesis parameters recorded by the last ranking pass. Nothing
here decides anything — it reads the ledger after the fact, which is why it
lives outside ``core`` and outside the worker.

The report answers one question in three ways:

* What does it cost to buy rank 1 under **StakeRoute**?
* What does the identical attack cost under **majority vote**?
* What does it cost under **highest confidence**?

and then decomposes StakeRoute's answer, because the honest version of this
analysis has to separate two different defences that happen to both be
active. A false hypothesis about a low-impact subsystem is hard to promote
partly because the attention allocator weights impact and urgency — that is
a *policy* defence, and it would protect a naive strategy just as well if
one bothered to apply it. What is specific to the market is the *economic*
defence: the reputation-weighted, stake-dampened influence an attacker has
to actually acquire. Reporting only the combined number would overstate the
mechanism, so the report gives both.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from stakeroute.core.attack import (
    attack_frontier,
    baseline_attack_cost,
    stakeroute_attack_cost,
)
from stakeroute.core.types import PROBABILITY_CEIL
from stakeroute.storage.repository import Repository

REPUTATION_SWEEP: tuple[float, ...] = (0.1, 0.25, 0.5, 0.75, 1.0)
"""Per-identity reputation levels for the frontier.

0.1 is the floor a freshly created identity starts at — the only level an
attacker gets for free. 1.0 is the ceiling, reachable only by a long record
of well-calibrated forecasts, which is the entire cost being measured.
"""


class NoRankingRecorded(Exception):
    """No ranking pass has been recorded yet, so there is nothing to price."""


def _honest_weight(contributions_json: str) -> float:
    contributions = json.loads(contributions_json)
    return sum(c["weight"] for c in contributions)


def build_report(
    repo: Repository,
    tenant_id: str,
    target_hypothesis_id: str | None = None,
    stake_per_identity: int = 50,
    shared_evidence_cluster: bool = False,
) -> dict:
    """Return the cost-of-attack report for the most recent ranking pass.

    ``target_hypothesis_id`` defaults to the runner-up — the hypothesis an
    adversary would actually try to promote, since it starts closest to the
    slot being contested. ``stake_per_identity`` defaults to the maximum
    stake a single forecast may carry, which is the attacker's cheapest
    route to influence: weight grows as ``sqrt(stake)``, so concentrating
    capital in fewer, larger forecasts costs less than spreading it.

    Raises:
        NoRankingRecorded: if no ranking pass has been recorded.
    """
    decisions = sorted(
        repo.latest_decisions(tenant_id, "stakeroute"), key=lambda r: r["rank"]
    )
    if len(decisions) < 2:
        raise NoRankingRecorded(
            "cost-of-attack needs at least two ranked hypotheses; run a scenario first"
        )

    defender_row = decisions[0]
    if target_hypothesis_id is None:
        target_row = decisions[1]
    else:
        target_row = next(
            (d for d in decisions if d["hypothesis_id"] == target_hypothesis_id),
            None,
        )
        if target_row is None:
            raise NoRankingRecorded(f"no ranking recorded for {target_hypothesis_id!r}")
        if target_row["hypothesis_id"] == defender_row["hypothesis_id"]:
            raise NoRankingRecorded(
                "the target already holds rank 1; there is nothing to attack"
            )

    target = repo.get_hypothesis(target_row["hypothesis_id"])
    defender = repo.get_hypothesis(defender_row["hypothesis_id"])
    if target is None or defender is None:
        raise NoRankingRecorded("ranked hypothesis missing from the ledger")

    honest_weight = _honest_weight(target_row["contributions"])
    honest_probability = target_row["aggregated_probability"]

    forecasts = repo.list_forecasts_for_hypothesis(target["id"])
    honest_forecast_count = len(forecasts)
    honest_votes_true = sum(1 for f in forecasts if f["probability"] > 0.5)
    current_max_probability = max(
        (f["probability"] for f in forecasts), default=target["prior_probability"]
    )

    common = {
        "honest_weight": honest_weight,
        "honest_probability": honest_probability,
        "stake_per_identity": stake_per_identity,
        "shared_evidence_cluster": shared_evidence_cluster,
        "prior_probability": target["prior_probability"],
    }

    stakeroute_cost = stakeroute_attack_cost(
        defender_priority=defender_row["priority"],
        impact_minor_units=target["impact_minor_units"],
        urgency=target["urgency"],
        review_cost=target["review_cost"],
        reputation_per_identity=0.1,
        **common,
    )

    # The same attack with the impact-weighting advantage removed: what the
    # market alone is worth, priced honestly.
    economic_only = stakeroute_attack_cost(
        defender_priority=defender_row["aggregated_probability"]
        * target["impact_minor_units"]
        * target["urgency"]
        / target["review_cost"],
        impact_minor_units=target["impact_minor_units"],
        urgency=target["urgency"],
        review_cost=target["review_cost"],
        reputation_per_identity=0.1,
        **common,
    )

    baselines = {
        strategy: baseline_attack_cost(
            strategy=strategy,
            defender_probability=_baseline_defender_probability(
                repo, tenant_id, strategy
            ),
            honest_forecast_count=honest_forecast_count,
            honest_votes_true=honest_votes_true,
            current_max_probability=current_max_probability,
        )
        for strategy in ("majority_vote", "highest_confidence")
    }

    frontier = attack_frontier(
        defender_priority=defender_row["aggregated_probability"]
        * target["impact_minor_units"]
        * target["urgency"]
        / target["review_cost"],
        impact_minor_units=target["impact_minor_units"],
        urgency=target["urgency"],
        review_cost=target["review_cost"],
        honest_weight=honest_weight,
        honest_probability=honest_probability,
        reputations=REPUTATION_SWEEP,
        stake_per_identity=stake_per_identity,
        shared_evidence_cluster=shared_evidence_cluster,
        prior_probability=target["prior_probability"],
    )

    return {
        "defender": {
            "hypothesis_id": defender_row["hypothesis_id"],
            "statement": defender["statement"],
            "probability": defender_row["aggregated_probability"],
            "priority": defender_row["priority"],
            "impact_minor_units": defender["impact_minor_units"],
        },
        "target": {
            "hypothesis_id": target_row["hypothesis_id"],
            "statement": target["statement"],
            "probability": honest_probability,
            "priority": target_row["priority"],
            "impact_minor_units": target["impact_minor_units"],
            "honest_weight": honest_weight,
            "honest_forecast_count": honest_forecast_count,
        },
        "strategies": {
            "stakeroute": asdict(stakeroute_cost),
            **{name: asdict(cost) for name, cost in baselines.items()},
        },
        "economic_defence_only": asdict(economic_only),
        "frontier": [asdict(point) for point in frontier],
        "assumptions": {
            "adversary_probability": PROBABILITY_CEIL,
            "stake_per_identity": stake_per_identity,
            "reputation_per_identity": 0.1,
            "shared_evidence_cluster": shared_evidence_cluster,
            "note": (
                "New identities start at the reputation floor (0.1), so that "
                "is what a Sybil flood gets. Each attacking identity is "
                "assumed to assert the probability ceiling and to cite its "
                "own distinct evidence group unless stated otherwise — the "
                "cheapest attack available, not the most convenient one to "
                "defend against."
            ),
        },
    }


def _baseline_defender_probability(
    repo: Repository, tenant_id: str, strategy: str
) -> float:
    """Return the score currently holding rank 1 *under that baseline*.

    A baseline ranks by its raw probability and never folds in impact, so
    the bar an attacker must clear against it is that probability — and the
    number is not the one StakeRoute would report for the same hypothesis.
    Charging the attacker StakeRoute's impact-weighted bar would flatter
    the baseline badly.

    Whatever sits at rank 1 under the baseline is the bar, even when that
    is already the false hypothesis: a strategy that has *been* flipped
    reports a cost of zero, which is the correct answer.
    """
    rows = repo.latest_decisions(tenant_id, strategy)
    if not rows:
        raise NoRankingRecorded(f"no {strategy} ranking recorded")
    leader = min(rows, key=lambda r: r["rank"])
    return leader["aggregated_probability"]


def _format_report(report: dict) -> str:
    """Render the report as a fixed-width table for the terminal."""
    lines = [
        "COST OF ATTACK — what it takes to buy rank 1",
        "",
        f"  defender : {report['defender']['statement']} "
        f"({report['defender']['probability']:.1%})",
        f"  target   : {report['target']['statement']} "
        f"({report['target']['probability']:.1%}, "
        f"{report['target']['honest_forecast_count']} honest forecasts, "
        f"weight {report['target']['honest_weight']:.2f})",
        "",
        f"  {'strategy':<28}{'identities':>11}{'credits':>9}{'lost':>7}  verdict",
        f"  {'-' * 28}{'-' * 11}{'-' * 9}{'-' * 7}  {'-' * 24}",
    ]

    rows = [
        ("highest confidence", report["strategies"]["highest_confidence"]),
        ("majority vote", report["strategies"]["majority_vote"]),
        ("StakeRoute (as ranked)", report["strategies"]["stakeroute"]),
        ("StakeRoute (market only)", report["economic_defence_only"]),
    ]
    for label, cost in rows:
        if not cost["feasible"]:
            lines.append(f"  {label:<28}{'—':>11}{'—':>9}{'—':>7}  not purchasable")
            continue
        verdict = (
            "free — identities only"
            if cost["credits"] == 0
            else f"{cost['credits']} credits at risk"
        )
        lines.append(
            f"  {label:<28}{cost['identities']:>11}{cost['credits']:>9}"
            f"{cost['settlement_loss_credits']:>7}  {verdict}"
        )

    lines += ["", "  identities needed, by reputation the attacker already holds:"]
    for point in report["frontier"]:
        if not point["feasible"]:
            continue
        bar = "█" * min(point["identities"], 40)
        lines.append(
            f"    rep {point['reputation_per_identity']:.2f}  "
            f"{point['identities']:>3}  {bar}"
        )

    lines += ["", f"  {report['assumptions']['note']}"]
    return "\n".join(lines)


def main() -> None:
    """Print the cost-of-attack report for the recorded run."""
    from stakeroute.config import DB_PATH, DEFAULT_TENANT_ID

    repo = Repository(DB_PATH)
    try:
        print(_format_report(build_report(repo, DEFAULT_TENANT_ID)))
    except NoRankingRecorded as exc:
        print(f"nothing to price: {exc}")
    finally:
        repo.close()


if __name__ == "__main__":
    main()
