"""Economic security: what it costs an adversary to buy the top slot.

The rest of the core answers "what does the mechanism decide?". This module
answers the question a tokenomics judge actually asks next: *how much would
it cost to make it decide something else?*

Because aggregation and ranking are closed-form, the answer is closed-form
too — no search, no simulation. For a defender hypothesis ``D`` holding rank
1 and a target hypothesis ``T`` the adversary wants promoted:

1. Ranking is ``priority = p * impact * urgency / review_cost``, so the
   aggregate probability ``T`` must reach to tie ``D`` is a division
   (:func:`required_probability`). If that lands above the probability
   ceiling, no attack succeeds at any price — impact and urgency alone
   decide it.
2. Aggregation is a weighted mean, so the adversarial influence weight
   needed to drag ``T``'s aggregate up to that probability is a division
   too (:func:`required_adversary_weight`).
3. Influence weight is ``reputation * sqrt(stake) * independence``, so that
   weight converts into a concrete identity count and credit bill
   (:func:`sybil_identities_required`).

The same three steps run against the two baseline strategies, which is the
comparison that matters: under majority vote the bill is denominated purely
in *identities*, and under highest-confidence it is a single forecast. Only
StakeRoute prices the attack in a resource — earned reputation — that an
attacker cannot mint on demand.

Constitution Principle I applies here as everywhere in the core: pure
functions, explicit arguments, no I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from stakeroute.core.settlement import settle_forecast
from stakeroute.core.types import PROBABILITY_CEIL, DomainError


class InfeasibleAttack(DomainError):
    """Raised for parameters that describe no attack at all.

    Distinct from an attack that is merely unaffordable: an unaffordable
    attack is reported as ``feasible=False`` on an :class:`AttackCost`,
    which is a finding. This exception means the caller passed nonsense.
    """


@dataclass(frozen=True, slots=True)
class AttackCost:
    """The price of moving one hypothesis to rank 1 under one strategy.

    ``feasible=False`` means no quantity of identities, stake, or
    reputation achieves it — the target's impact and urgency are low enough
    that even a certainty-level probability cannot outrank the defender.
    """

    strategy: str
    feasible: bool
    required_probability: float
    required_weight: float
    identities: int
    credits: int
    reputation_per_identity: float
    stake_per_identity: int
    shared_evidence_cluster: bool
    note: str
    settlement_loss_credits: int = 0
    """Credits destroyed at settlement once the target resolves false.

    The up-front bill is only half the price. Because settlement scores
    every forecast against ground truth, an attack that succeeds today is
    still paid for tomorrow — and against a strategy that never reads
    stake, this is structurally zero.
    """


def required_probability(
    defender_priority: float,
    impact_minor_units: int,
    urgency: float,
    review_cost: float,
) -> float:
    """Return the aggregate probability the target must reach to tie the
    defender's priority score.

    Inverts :func:`stakeroute.core.ranking.priority_score` for ``p``. The
    result is not clamped: a value above 1.0 is the meaningful signal that
    the target cannot be promoted at any probability, and callers depend on
    seeing it.

    Raises:
        InfeasibleAttack: if ``review_cost`` is not strictly positive, or if
            ``impact_minor_units * urgency`` is not strictly positive — a
            hypothesis with no impact has no priority to invert.
    """
    if review_cost <= 0:
        raise InfeasibleAttack(f"review_cost must be > 0, got {review_cost!r}")
    scale = impact_minor_units * urgency
    if scale <= 0:
        raise InfeasibleAttack(
            f"impact * urgency must be > 0, got {impact_minor_units!r} * {urgency!r}"
        )
    return defender_priority * review_cost / scale


def required_adversary_weight(
    honest_weight: float,
    honest_probability: float,
    target_probability: float,
    adversary_probability: float = PROBABILITY_CEIL,
) -> float | None:
    """Return the total influence weight an adversary must add to drag a
    weighted mean from ``honest_probability`` up to ``target_probability``.

    Solves ``(W_h*p_h + W_a*p_a) / (W_h + W_a) >= p*`` for ``W_a``, giving
    ``W_a = W_h * (p* - p_h) / (p_a - p*)``.

    Returns ``0.0`` when the target is already at or above the threshold, or
    when there is no honest weight to overcome — in the latter case the
    adversary sets the aggregate unopposed, so the infimum is zero and any
    positive weight suffices. Callers that need an identity count must treat
    a zero here as "one identity" unless the target already leads; see
    :func:`stakeroute_attack_cost`, which distinguishes the two.

    Returns ``None`` when the attack is infeasible at any weight: the
    adversary's own probability is at or below the threshold, so adding
    weight cannot pull the mean high enough.
    """
    if honest_probability >= target_probability:
        return 0.0
    if honest_weight <= 0:
        return 0.0
    if adversary_probability <= target_probability:
        return None
    gap = target_probability - honest_probability
    headroom = adversary_probability - target_probability
    return honest_weight * gap / headroom


def sybil_identities_required(
    weight: float,
    reputation_per_identity: float,
    stake_per_identity: int,
    shared_evidence_cluster: bool = False,
) -> int | None:
    """Return how many identities are needed to muster ``weight``.

    With one distinct evidence cluster per identity, independence is 1 and
    total weight is linear in the identity count::

        W = N * r * sqrt(s)        =>  N = W / (r * sqrt(s))

    With every identity citing the *same* cluster, each is discounted by
    ``1/sqrt(N)`` and the count required is the square of that::

        W = N * r * sqrt(s) / sqrt(N) = r * sqrt(s*N)
                                   =>  N = (W / (r * sqrt(s)))^2

    That squaring is the independence discount doing its job. It is also
    precisely the bound on what the discount can do: an adversary willing to
    manufacture genuinely distinct evidence groups pays only the linear
    price, so evidence independence buys resistance to *lazy* correlated
    flooding, not to a patient attacker. Reputation, not the discount, is
    what makes the linear price high.

    Returns ``None`` when no finite count suffices (non-positive reputation
    or stake). Returns ``0`` only for a non-positive ``weight`` requirement.
    """
    if weight <= 0:
        return 0
    if reputation_per_identity <= 0 or stake_per_identity <= 0:
        return None
    unit = reputation_per_identity * math.sqrt(stake_per_identity)
    identities = weight / unit
    if shared_evidence_cluster:
        identities = identities**2
    return max(1, math.ceil(identities))


def attack_settlement_loss(
    identities: int,
    stake_per_identity: int,
    prior_probability: float,
    adversary_probability: float = PROBABILITY_CEIL,
    outcome: int = 0,
    scale: int = 100,
) -> int:
    """Return the credits the attack destroys when the target resolves.

    A successful attack is not a one-off purchase. Every Sybil forecast is
    scored against ground truth like any other, and a confident claim about
    a hypothesis that resolves false is the worst possible Brier score — so
    each identity forfeits close to its entire stake, bounded at exactly
    its stake by :func:`stakeroute.core.settlement.settle_forecast`.

    Returned as a positive number of credits lost. Zero when no capital was
    committed, which is precisely the baselines' situation.
    """
    if identities <= 0 or stake_per_identity <= 0:
        return 0
    per_identity = settle_forecast(
        forecast_id="attack-cost-estimate",
        stake=stake_per_identity,
        prior_probability=prior_probability,
        probability=adversary_probability,
        outcome=outcome,
        scale=scale,
    )
    return -per_identity.credit_delta * identities


def majority_vote_identities_required(
    honest_forecast_count: int,
    honest_votes_true: int,
    target_probability: float,
) -> int | None:
    """Return how many extra identities flip an unweighted majority vote.

    Each added forecast votes true, so the tally moves from ``v/n`` to
    ``(v+N)/(n+N)``. Solving for ``N`` gives
    ``N > (n*p* - v) / (1 - p*)``.

    The inequality is strict: taking rank 1 means *beating* the defender's
    score, and an exact tie breaks toward the higher-impact hypothesis —
    which, in the scenario this exists to analyse, is the genuine incident.
    Reputation and stake do not appear anywhere in the arithmetic; that is
    the entire point of the comparison.

    Returns ``None`` when the threshold is at or above 1.0, which no finite
    flood reaches.
    """
    if honest_forecast_count > 0:
        current = honest_votes_true / honest_forecast_count
        if current > target_probability:
            return 0
    elif target_probability <= 0:
        return 0
    if target_probability >= 1.0:
        return None
    needed = (honest_forecast_count * target_probability - honest_votes_true) / (
        1.0 - target_probability
    )
    return max(1, math.floor(needed) + 1)


def highest_confidence_identities_required(
    current_max_probability: float,
    target_probability: float,
) -> int | None:
    """Return how many identities flip a highest-confidence ranking.

    One, whenever the threshold is reachable at all: the strategy reads the
    single largest self-reported probability, so one unbacked assertion at
    the ceiling overrides an entire calibrated population. Returns ``None``
    only when the threshold is at or above the probability ceiling, leaving
    no room to assert a strictly larger number.
    """
    if current_max_probability > target_probability:
        return 0
    if target_probability >= PROBABILITY_CEIL:
        return None
    return 1


def stakeroute_attack_cost(
    defender_priority: float,
    impact_minor_units: int,
    urgency: float,
    review_cost: float,
    honest_weight: float,
    honest_probability: float,
    reputation_per_identity: float,
    stake_per_identity: int,
    shared_evidence_cluster: bool = False,
    adversary_probability: float = PROBABILITY_CEIL,
    prior_probability: float = 0.5,
) -> AttackCost:
    """Price a rank-1 attack against StakeRoute's weighted aggregation.

    Composes the three steps in the module docstring and converts the
    result into an identity count, a credit bill, and the settlement loss
    that bill incurs once the target hypothesis resolves false.
    """
    threshold = required_probability(
        defender_priority, impact_minor_units, urgency, review_cost
    )
    already_leading = honest_probability >= threshold
    weight = required_adversary_weight(
        honest_weight, honest_probability, threshold, adversary_probability
    )

    if weight is None:
        return AttackCost(
            strategy="stakeroute",
            feasible=False,
            required_probability=threshold,
            required_weight=math.inf,
            identities=0,
            credits=0,
            reputation_per_identity=reputation_per_identity,
            stake_per_identity=stake_per_identity,
            shared_evidence_cluster=shared_evidence_cluster,
            note=(
                f"unreachable: the target needs an aggregate of "
                f"{threshold:.3f} to tie, above the {adversary_probability:.2f} "
                "an adversary can assert — impact and urgency decide it, not stake"
            ),
        )

    identities = sybil_identities_required(
        weight, reputation_per_identity, stake_per_identity, shared_evidence_cluster
    )
    if identities is None:
        return AttackCost(
            strategy="stakeroute",
            feasible=False,
            required_probability=threshold,
            required_weight=weight,
            identities=0,
            credits=0,
            reputation_per_identity=reputation_per_identity,
            stake_per_identity=stake_per_identity,
            shared_evidence_cluster=shared_evidence_cluster,
            note="no finite identity count: zero reputation or zero stake",
        )

    if identities == 0 and not already_leading:
        # No honest weight to overcome — one identity sets the aggregate.
        identities = 1

    if already_leading:
        note = "already at or above the threshold; no attack required"
    else:
        cluster = "one shared" if shared_evidence_cluster else "distinct"
        note = (
            f"{identities} identities at reputation "
            f"{reputation_per_identity:.2f} and {stake_per_identity} staked "
            f"credits each, citing {cluster} evidence, to reach "
            f"{threshold:.3f}"
        )

    return AttackCost(
        strategy="stakeroute",
        feasible=True,
        required_probability=threshold,
        required_weight=weight,
        identities=identities,
        credits=identities * stake_per_identity,
        reputation_per_identity=reputation_per_identity,
        stake_per_identity=stake_per_identity,
        shared_evidence_cluster=shared_evidence_cluster,
        note=note,
        settlement_loss_credits=attack_settlement_loss(
            identities, stake_per_identity, prior_probability, adversary_probability
        ),
    )


def baseline_attack_cost(
    strategy: str,
    defender_probability: float,
    honest_forecast_count: int,
    honest_votes_true: int,
    current_max_probability: float,
    stake_per_identity: int = 0,
) -> AttackCost:
    """Price the same rank-1 attack against a baseline strategy.

    The threshold is the defender's *probability*, not its priority: a
    naive baseline ranks by its own raw score and never folds in impact,
    urgency, or review cost (see ``worker.pipeline._run_one_strategy``).
    Pricing it against StakeRoute's impact-weighted bar would flatter the
    baseline by charging the attacker for an obstacle it does not face.

    ``stake_per_identity`` defaults to zero because neither baseline reads
    stake: an attacker against them commits no capital at all, and the
    credit column should say so rather than quietly borrowing StakeRoute's
    assumptions.

    Raises:
        InfeasibleAttack: for an unknown strategy name.
    """
    threshold = defender_probability

    if strategy == "majority_vote":
        identities = majority_vote_identities_required(
            honest_forecast_count, honest_votes_true, threshold
        )
        basis = "unweighted votes; reputation and stake are not read"
    elif strategy == "highest_confidence":
        identities = highest_confidence_identities_required(
            current_max_probability, threshold
        )
        basis = "one asserted probability overrides the whole population"
    else:
        raise InfeasibleAttack(f"unknown baseline strategy: {strategy!r}")

    if identities is None:
        return AttackCost(
            strategy=strategy,
            feasible=False,
            required_probability=threshold,
            required_weight=math.inf,
            identities=0,
            credits=0,
            reputation_per_identity=0.0,
            stake_per_identity=stake_per_identity,
            shared_evidence_cluster=False,
            note=f"unreachable: needs an aggregate of {threshold:.3f}",
        )

    plural = "identity" if identities == 1 else "identities"
    note = (
        "already at or above the threshold; no attack required"
        if identities == 0
        else f"{identities} {plural}, {basis}"
    )
    return AttackCost(
        strategy=strategy,
        feasible=True,
        required_probability=threshold,
        required_weight=0.0,
        identities=identities,
        credits=identities * stake_per_identity,
        reputation_per_identity=0.0,
        stake_per_identity=stake_per_identity,
        shared_evidence_cluster=False,
        note=note,
    )


def attack_frontier(
    defender_priority: float,
    impact_minor_units: int,
    urgency: float,
    review_cost: float,
    honest_weight: float,
    honest_probability: float,
    reputations: tuple[float, ...],
    stake_per_identity: int,
    shared_evidence_cluster: bool = False,
    prior_probability: float = 0.5,
) -> tuple[AttackCost, ...]:
    """Sweep per-identity reputation and return the cost at each level.

    This is the curve worth showing: the identity count needed falls as
    ``1/r``, so an attacker holding floor reputation pays an order of
    magnitude more than one who has already earned standing. Reputation is
    the scarce input, and it is only obtainable by forecasting well.
    """
    return tuple(
        stakeroute_attack_cost(
            defender_priority=defender_priority,
            impact_minor_units=impact_minor_units,
            urgency=urgency,
            review_cost=review_cost,
            honest_weight=honest_weight,
            honest_probability=honest_probability,
            reputation_per_identity=reputation,
            stake_per_identity=stake_per_identity,
            shared_evidence_cluster=shared_evidence_cluster,
            prior_probability=prior_probability,
        )
        for reputation in reputations
    )
