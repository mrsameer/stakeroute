# Phase 1 Data Model: StakeRoute

**Feature**: 001-stakeroute-attention-market
**Date**: 2026-08-29
**Depends on**: [research.md](./research.md)

Every table carries `tenant_id` from the first migration (Constitution, Additional Constraints),
even though the demonstration uses a single tenant. All monetary quantities are integers in whole
credit units (D-002). Timestamps are stored as integer epoch milliseconds so that ordering is
total and engine-independent.

---

## Entities

### `tenants`

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | Demo uses a single row, `acmepay` |
| `name` | TEXT NOT NULL | |
| `created_at_ms` | INTEGER NOT NULL | |

---

### `agents`

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | |
| `tenant_id` | TEXT NOT NULL FK → tenants | |
| `display_name` | TEXT NOT NULL | |
| `reputation` | REAL NOT NULL | Bounded `[0.1, 1.0]`, see rules below |
| `available_credits` | INTEGER NOT NULL | Unstaked balance |
| `staked_credits` | INTEGER NOT NULL | Locked against open hypotheses |
| `attested` | INTEGER NOT NULL | Boolean. Identity attested by the enterprise (FR-011) |
| `created_at_ms` | INTEGER NOT NULL | Drives "newly created agent" detection |

**Invariants**
- `0.1 ≤ reputation ≤ 1.0` — enforced by clamping at every write, never by assertion alone.
- `available_credits ≥ 0` and `staked_credits ≥ 0` at all times.
- `available_credits + staked_credits` changes only through a `settlements` row or an epoch grant.
- New agents are created at the reputation floor plus a small margin, never at parity (FR-010).

**State transitions**: reputation moves only in `settle_hypothesis`. It decays toward the
population mean on epoch rollover so that historical standing cannot persist indefinitely (FR-027).

---

### `events`

The idempotency boundary. One row per distinct observation, ever.

| Field | Type | Notes |
|---|---|---|
| `event_id` | TEXT PK | `sha256(tenant_id\|source\|source_event_id\|floor(ts_ms/1000))` (D-005) |
| `tenant_id` | TEXT NOT NULL FK → tenants | |
| `source` | TEXT NOT NULL | `logs` \| `metrics` \| `deploys` \| `security` \| `payments` |
| `source_event_id` | TEXT NOT NULL | Identifier as issued by the originating system |
| `observed_at_ms` | INTEGER NOT NULL | |
| `ingested_at_ms` | INTEGER NOT NULL | |
| `provenance` | TEXT NOT NULL | JSON: originating system, collector, raw reference |
| `payload` | TEXT NOT NULL | JSON: normalised observation body |

**Constraints**
- `UNIQUE(event_id)` — the single mechanism that makes redelivery safe (FR-003).
- Insert is `ON CONFLICT DO NOTHING`; a zero-row result means "already applied, skip effect".
- `INDEX(tenant_id, observed_at_ms)` for replay ordering.

---

### `evidence_clusters`

The unit over which correlation is discounted (FR-015).

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | e.g. `database-observability` |
| `tenant_id` | TEXT NOT NULL FK → tenants | |
| `label` | TEXT NOT NULL | Human-readable, shown in the UI |
| `member_count` | INTEGER NOT NULL | Distinct forecasts currently citing this cluster |

**Note**: `member_count` is derived and recomputed per ranking pass rather than incrementally
maintained, so it cannot drift under redelivery.

---

### `hypotheses`

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | |
| `tenant_id` | TEXT NOT NULL FK → tenants | |
| `statement` | TEXT NOT NULL | e.g. `payment_gateway_failure` |
| `prior_probability` | REAL NOT NULL | Clamped to `[0.01, 0.99]` (D-009) |
| `impact_minor_units` | INTEGER NOT NULL | Estimated business impact, integer currency units |
| `urgency` | REAL NOT NULL | Decay factor from time remaining to deadline |
| `review_cost` | REAL NOT NULL | Relative cost of a human investigating this |
| `deadline_ms` | INTEGER NOT NULL | |
| `status` | TEXT NOT NULL | `open` \| `resolved` \| `expired` |
| `created_at_ms` | INTEGER NOT NULL | Used for time-to-attention |

**State transitions**
```
open ──resolve(outcome)──> resolved     (settlement runs, stakes released net of result)
open ──deadline passes───> expired      (stakes returned in full, reputation unchanged, D/FR-028)
```
`resolved` and `expired` are terminal. No transition out of either.

---

### `forecasts`

An agent's staked probabilistic claim.

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | |
| `tenant_id` | TEXT NOT NULL FK → tenants | |
| `hypothesis_id` | TEXT NOT NULL FK → hypotheses | |
| `agent_id` | TEXT NOT NULL FK → agents | |
| `probability` | REAL NOT NULL | Clamped to `[0.01, 0.99]` |
| `stake` | INTEGER NOT NULL | `1 ≤ stake ≤ 50`, and `≤ agent.available_credits` at submission |
| `evidence_cluster_id` | TEXT NOT NULL FK → evidence_clusters | |
| `evidence_refs` | TEXT NOT NULL | JSON array of contributing `event_id`s |
| `source_event_id` | TEXT NOT NULL | Idempotency link back to `events` |
| `created_at_ms` | INTEGER NOT NULL | |
| `expires_at_ms` | INTEGER NOT NULL | |

**Constraints**
- `UNIQUE(hypothesis_id, agent_id)` — one live forecast per agent per hypothesis. A resubmission
  replaces the prior forecast and adjusts the stake difference, rather than accumulating votes.
- Rejected at submission if stake exceeds available credits, or probability or stake is out of
  range (FR-008). Rejection is recorded, not silently discarded.

---

### `attention_decisions`

The audit record of one ranking pass.

| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `tenant_id` | TEXT NOT NULL FK → tenants | |
| `hypothesis_id` | TEXT NOT NULL FK → hypotheses | |
| `strategy` | TEXT NOT NULL | `stakeroute` \| `majority_vote` \| `highest_confidence` |
| `aggregated_probability` | REAL NOT NULL | |
| `priority` | REAL NOT NULL | `P × impact × urgency ÷ review_cost` |
| `rank` | INTEGER NOT NULL | 1-based |
| `routed` | INTEGER NOT NULL | Boolean: did it fit inside the attention budget |
| `reason` | TEXT NOT NULL | Human-readable explanation (FR-021) |
| `contributions` | TEXT NOT NULL | JSON: per-forecast weight, independence factor, normalised α |
| `decided_at_ms` | INTEGER NOT NULL | |

The three strategies are written for every pass over the same event stream, which is what makes
the side-by-side comparison (FR-023, FR-032) a stored fact rather than a UI trick.

---

### `outcomes`

| Field | Type | Notes |
|---|---|---|
| `hypothesis_id` | TEXT PK FK → hypotheses | |
| `tenant_id` | TEXT NOT NULL FK → tenants | |
| `outcome` | INTEGER NOT NULL | 1 true, 0 false |
| `resolved_at_ms` | INTEGER NOT NULL | |
| `resolved_by` | TEXT NOT NULL | `simulator` \| `operator` |

---

### `settlements`

| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `tenant_id` | TEXT NOT NULL FK → tenants | |
| `forecast_id` | TEXT NOT NULL FK → forecasts | |
| `brier_score` | REAL NOT NULL | `(p − y)²` |
| `prior_brier_score` | REAL NOT NULL | `(p₀ − y)²` |
| `improvement` | REAL NOT NULL | `prior_brier − brier` |
| `credit_delta` | INTEGER NOT NULL | `round_half_even(stake × improvement × SCALE) / SCALE`, floored at `−stake` |
| `reputation_before` | REAL NOT NULL | |
| `reputation_after` | REAL NOT NULL | |
| `settled_at_ms` | INTEGER NOT NULL | |

**Constraints**
- `UNIQUE(forecast_id)` — the second half of the idempotency guarantee. A redelivered resolution
  cannot settle a forecast twice (FR-003, SC-005).
- `credit_delta ≥ −stake` — enforced in code and asserted in the schema check (FR-026, SC-009).

---

### `epochs`

| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `tenant_id` | TEXT NOT NULL FK → tenants | |
| `started_at_ms` | INTEGER NOT NULL | |
| `grant_per_agent` | INTEGER NOT NULL | Default 100 |
| `seed` | INTEGER NOT NULL | Scenario seed; makes the run reproducible (D-009, SC-006) |

---

## Derived values (computed, never stored)

| Value | Formula | Requirement |
|---|---|---|
| `independence_i` | `1 / √(cluster_size_i)` | FR-015 |
| `w_i` | `reputation_i × √stake_i × independence_i` | FR-013, FR-014 |
| `α_i` | `w_i / Σ_j w_j` | FR-016 |
| `P(h)` | `Σ_i α_i · p_i` | FR-016 |
| `priority(h)` | `P(h) × impact × urgency ÷ review_cost` | FR-019 |
| `precision@K` | routed hypotheses that resolved true ÷ K | FR-033 |
| `false_escalation_rate` | routed hypotheses that resolved false ÷ routed | FR-033 |
| `time_to_attention` | `decided_at_ms − created_at_ms` for true incidents | FR-033 |

Recomputing rather than storing these is deliberate: a stored aggregate can drift out of sync with
its inputs under redelivery, and Principle II requires that every displayed number be derivable
from what is stored beside it.

---

## Entity relationships

```
tenants ──┬── agents ──────────── forecasts ──── settlements
          ├── events                  │
          ├── evidence_clusters ──────┤
          ├── hypotheses ─────────────┴── attention_decisions
          │        └── outcomes
          └── epochs
```
