# Contract: Deterministic Core Library

**Module**: `src/stakeroute/core/`
**Constitution**: Principle I (non-negotiable), Principle II, D-001, D-009

The core is the mechanism. It is pure: no I/O, no async, no clock reads, no unseeded randomness,
no imports from `stakeroute.storage` or `stakeroute.transport`. Violation of this boundary is
enforced by an import-linting test, not by convention.

All functions are total: given valid inputs they return a value or raise a typed domain error.
None of them can partially apply an effect.

## Inputs (frozen dataclasses)

```
AgentSnapshot(id, reputation, available_credits, staked_credits, attested, created_at_ms)
ForecastSnapshot(id, agent_id, hypothesis_id, probability, stake, evidence_cluster_id)
HypothesisSnapshot(id, statement, prior_probability, impact_minor_units,
                   deadline_ms, review_cost, created_at_ms)
```

A ranking pass receives a complete snapshot. The core never fetches.

## Public functions

### `independence_factor(cluster_size: int) -> float`
Returns `1 / sqrt(cluster_size)`. Raises `DomainError` for `cluster_size < 1`.
*Satisfies FR-015.*

### `influence_weight(reputation: float, stake: int, independence: float) -> float`
Returns `reputation * sqrt(stake) * independence`. Sub-linear in stake by construction.
*Satisfies FR-013, FR-014.*

### `aggregate_probability(forecasts, agents, cluster_sizes) -> AggregationResult`
Returns the aggregated probability **and** the per-forecast weight, independence factor and
normalised `α` that produced it. The explanation is part of the return value, not a separate
call — Principle II is enforced by making the unexplained form unrepresentable.
Returns the hypothesis prior when the forecast set is empty.
*Satisfies FR-016, FR-017.*

### `priority_score(probability, impact_minor_units, urgency, review_cost) -> float`
Returns `probability * impact * urgency / review_cost`. Raises on `review_cost <= 0`.
*Satisfies FR-019.*

### `allocate_attention(ranked, budget: int) -> AllocationResult`
Returns the routed slice, the withheld count, and a reason string per hypothesis. Never returns
more than `budget` entries. Ties break on `(-priority, -impact, hypothesis_id)`.
*Satisfies FR-020, FR-021, FR-022, SC-004.*

### `brier_score(probability: float, outcome: int) -> float`
Returns `(p - y)^2`. Raises for `outcome not in (0, 1)`.
*Satisfies FR-025.*

### `settle_forecast(forecast, prior_probability, outcome) -> Settlement`
Returns an integer `credit_delta` computed from `stake × (prior_brier − brier)`, scaled and
rounded half-to-even, floored at `−stake`. Never returns a loss exceeding the stake.
*Satisfies FR-026, SC-009.*

### `update_reputation(current: float, settlement, decay: float) -> float`
Returns the new reputation clamped to `[0.1, 1.0]`, weighting recent results above older ones.
*Satisfies FR-027.*

### `clamp_probability(p: float) -> float`
Returns `p` clamped to `[0.01, 0.99]`. Applied at every ingress so scoring stays well defined.
*Satisfies D-009.*

## Baseline strategies (same signature as the StakeRoute ranker)

### `rank_majority_vote(forecasts, ...) -> list[RankedHypothesis]`
One unweighted vote per forecast above 0.5. Deliberately Sybil-vulnerable — this is the baseline
that must visibly fail in the attack demo.

### `rank_highest_confidence(forecasts, ...) -> list[RankedHypothesis]`
Ranks by maximum self-reported probability. Deliberately vulnerable to the confidently-wrong agent.

*Both satisfy FR-023 and are required for SC-002.*

## Domain errors

`InvalidProbability`, `InvalidStake`, `InsufficientCredits`, `EmptyCluster`, `InvalidOutcome`.
All carry the offending value. None are caught inside the core.

## Determinism guarantees

- Identical inputs produce identical outputs, bit for bit.
- Iteration is over sorted identifiers; no dict or set ordering is observable in a result.
- No function reads a clock. Times are parameters.
- No function constructs an RNG. Randomness belongs to the simulator alone.
