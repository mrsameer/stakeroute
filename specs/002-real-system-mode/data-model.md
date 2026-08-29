# Phase 1 Data Model — Real System Mode

Every entity from feature 001 carries forward unchanged. This document covers the additions and the
three modifications to existing tables. DDL stays Postgres-portable (D-004) and `tenant_id` is
present on every table (Constitution, Additional Constraints).

Two rules govern the whole schema and are worth stating before the tables:

1. **Nothing unredacted is stored.** Payload columns hold post-redaction content only (D-014).
2. **Numbers that reach `priority_score` are never model-authored.** `attribute_estimates` rows are
   written by `core/estimates.py`; `proposals` rows are written from model output. The two tables
   are separate precisely so this is visible in the schema (D-016).

---

## Modifications to existing tables

### `mode` column (D-019)

Added to `events`, `hypotheses`, `forecasts`, `attention_decisions`, `outcomes`, `settlements`:

```sql
ALTER TABLE <t> ADD COLUMN mode TEXT NOT NULL DEFAULT 'sim';
```

**Values**: `'real' | 'sim' | 'replay'`. The default is what keeps every feature-001 row and code
path unchanged (SC-108). No existing query filters on it; new real-mode queries do.

### `hypotheses` — provenance of the hypothesis itself

```sql
ALTER TABLE hypotheses ADD COLUMN proposal_id TEXT REFERENCES proposals(id);
ALTER TABLE hypotheses ADD COLUMN condition_name TEXT;
ALTER TABLE hypotheses ADD COLUMN condition_params TEXT;   -- JSON, NULL when operator-resolved
```

`proposal_id` is `NULL` for the seeded scenario's hand-written hypotheses. A non-null
`condition_name` is what makes a hypothesis automatically resolvable (D-017, FR-148).

### `forecasts` — the recorded evidence bundle

```sql
ALTER TABLE forecasts ADD COLUMN evidence_bundle TEXT;   -- JSON, NULL in sim mode
ALTER TABLE forecasts ADD COLUMN rationale TEXT;
ALTER TABLE forecasts ADD COLUMN interaction_id TEXT REFERENCES model_interactions(id);
```

`evidence_bundle` is the exact bundle handed to the agent. It is the artifact SC-102 is verified
over: a query asserting no bundle contains an outcome, ground-truth label or accuracy field, across
100% of real-mode forecasts. Storing it is the difference between an enforced exclusion and a
claimed one (FR-113).

---

## New entities

### `observation_sources` — a source is a record, so silence is representable

```sql
CREATE TABLE IF NOT EXISTS observation_sources (
    id TEXT PRIMARY KEY,                    -- 'host.metrics', 'app.logs', ...
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    display_name TEXT NOT NULL,
    state TEXT NOT NULL,                    -- 'live' | 'quiet' | 'silent' | 'absent'
    last_seen_ms INTEGER,                   -- NULL when never seen
    silence_threshold_ms INTEGER NOT NULL,
    absent_reason TEXT,                     -- e.g. 'docker binary not present'
    updated_at_ms INTEGER NOT NULL
);
```

**Why it exists**: FR-141. A source that has gone silent and a source with nothing to report are
indistinguishable unless the source itself has a state. `absent` is separate from `silent` because
"this host has no container runtime" is a permanent fact, not a fault.

**State transitions**: `absent` is terminal for a run (the binary does not appear mid-run).
`live → quiet` after one poll with no observations; `quiet → silent` after `silence_threshold_ms`;
any observation returns the source to `live`.

**Validation**: `state='absent'` requires a non-null `absent_reason`.

### `model_interactions` — the audit object that makes the boundary inspectable

```sql
CREATE TABLE IF NOT EXISTS model_interactions (
    id TEXT PRIMARY KEY,                    -- sha256 over (tenant|purpose|request_hash|requested_at_ms)
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    mode TEXT NOT NULL,
    purpose TEXT NOT NULL,                  -- 'proposal' | 'forecast' | 'explanation'
    agent_id TEXT,                          -- NULL for proposal and explanation
    request TEXT NOT NULL,                  -- redacted prompt, exactly as sent
    response TEXT,                          -- NULL on timeout or transport failure
    latency_ms INTEGER NOT NULL,
    accepted INTEGER NOT NULL,              -- 0/1
    rejection_reason TEXT,                  -- required when accepted = 0
    model_name TEXT NOT NULL,
    requested_at_ms INTEGER NOT NULL,
    UNIQUE (id)
);

CREATE INDEX IF NOT EXISTS idx_model_interactions_tenant_time
    ON model_interactions(tenant_id, requested_at_ms);
```

**Why it exists**: FR-123 requires every interaction recorded — request, response, latency, and the
accept-or-reject outcome. Rejections are rows, not absences (FR-122): a malformed response, an
out-of-range probability, a refusal and a timeout all produce a row with `accepted = 0`, and none of
them produces a forecast, hypothesis or economic effect.

`request` is the redacted prompt **as actually sent**. SC-115 is verified by scanning this column
for absolute paths, account names, environment values and credential-shaped strings across 100% of
rows — which only works because what is stored is the real outbound text.

**Validation**: `accepted = 0` requires `rejection_reason`. `accepted = 1` requires `response`.

### `proposals` — a candidate hypothesis before it is priced

```sql
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    mode TEXT NOT NULL,
    statement TEXT NOT NULL,
    cited_observation_ids TEXT NOT NULL,    -- JSON array of events.event_id
    condition_name TEXT,                    -- registry entry, NULL if none bound
    condition_params TEXT,                  -- JSON
    interaction_id TEXT NOT NULL REFERENCES model_interactions(id),
    status TEXT NOT NULL,                   -- 'pending' | 'promoted' | 'rejected' | 'merged'
    merged_into TEXT REFERENCES proposals(id),
    rejection_reason TEXT,
    created_at_ms INTEGER NOT NULL
);
```

**Why it exists**: FR-107 and FR-111. A proposal is not a hypothesis. It becomes one only after its
citations validate against real `events` rows, its condition binds to the registry, its attributes
are computed, and the whole thing is durably recorded — which is exactly what FR-111 requires before
anything enters the ranked queue.

`interaction_id` is `NOT NULL`: a proposal that did not come from a recorded interaction cannot
exist, which is what makes the proposal path auditable end to end.

**Validation**: every id in `cited_observation_ids` must exist in `events` for the same tenant.
`status='merged'` requires `merged_into` (D-023). `status='rejected'` requires `rejection_reason`.

### `attribute_estimates` — the number and the basis that produced it

```sql
CREATE TABLE IF NOT EXISTS attribute_estimates (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id),
    attribute TEXT NOT NULL,                -- 'impact' | 'urgency' | 'review_cost'
    value REAL NOT NULL,
    basis TEXT NOT NULL,                    -- human-readable derivation
    estimator TEXT NOT NULL,                -- pure function name in core/estimates.py
    confirmed_by_operator INTEGER NOT NULL, -- 0/1
    confirmed_at_ms INTEGER,
    superseded_by INTEGER REFERENCES attribute_estimates(id),
    created_at_ms INTEGER NOT NULL,
    UNIQUE (hypothesis_id, attribute, created_at_ms)
);
```

**Why it exists**: FR-108 and FR-109. `basis` is `NOT NULL` because Principle II makes an
unexplained number a defect — an estimate that cannot say how it was derived is not shippable.
`estimator` names the pure function, which is what ties a displayed number back to code the purity
test guards.

An operator correction (FR-109) inserts a **new row** and sets `superseded_by` on the old one.
Estimates are never updated in place, so the correction history is auditable.

**Validation**: `confirmed_by_operator = 1` requires `confirmed_at_ms`.

### `resolutions` — outcomes arriving from outside, including corrections

```sql
CREATE TABLE IF NOT EXISTS resolutions (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id),
    resolution_seq INTEGER NOT NULL,        -- 1 = first, 2+ = correction
    outcome INTEGER NOT NULL,               -- 0/1
    determination TEXT NOT NULL,            -- 'automatic' | 'operator'
    source TEXT NOT NULL,                   -- check name, or operator identity
    check_name TEXT,                        -- registry entry actually run
    check_params TEXT,                      -- JSON
    check_result TEXT,                      -- what the check returned, verbatim
    checked_at_ms INTEGER,
    arrived_at_ms INTEGER NOT NULL,
    settled INTEGER NOT NULL,               -- 0 when post-expiry (FR-138)
    not_settled_reason TEXT,
    dedup_key TEXT NOT NULL,
    UNIQUE (dedup_key),
    UNIQUE (hypothesis_id, resolution_seq)
);
```

**Why it exists**: the existing `outcomes` table has `hypothesis_id` as its primary key, so it can
hold exactly one outcome forever. That is correct for feature 001 and insufficient for three
requirements here: corrections must be new events rather than overwrites (FR-136), a post-expiry
arrival must be recorded without re-settling (FR-138), and the audit trail must name the check that
ran (FR-148).

`outcomes` is retained as the settlement trigger — the first resolution writes through to it — so
the settlement path and every feature-001 test are untouched.

**Idempotency**: `UNIQUE(dedup_key)` is the same pattern as `events.event_id` and
`settlements.forecast_id`. A redelivered outcome inserts zero rows and produces exactly one
settlement effect (FR-135, Principle III).

**Validation**: `determination='automatic'` requires `check_name`, `check_result` and
`checked_at_ms`. `settled = 0` requires `not_settled_reason`.

### `replay_runs` — the divergence report as a stored fact

```sql
CREATE TABLE IF NOT EXISTS replay_runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    source_window_start_ms INTEGER NOT NULL,
    source_window_end_ms INTEGER NOT NULL,
    started_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    identical INTEGER,                      -- NULL while running
    first_divergence TEXT,                  -- record type, id, expected, actual
    records_compared INTEGER NOT NULL,
    model_requests_made INTEGER NOT NULL    -- must be 0 (FR-130, SC-107)
);
```

**Why it exists**: FR-131 requires divergence be surfaced, not absorbed. Writing the comparison
result to the source database — while the replay itself runs against a scratch database (D-012) —
means a failed replay leaves evidence of its own failure rather than overwriting the record it
disagreed with.

`model_requests_made` is recorded rather than assumed. It is the direct evidence for SC-107's
"zero outbound model requests during replay", counted by the `RecordedModelClient`, which raises on
a cache miss.

---

## Non-persisted types (pure, in `stakeroute.core` and `stakeroute.real`)

These are frozen dataclasses, following feature 001's snapshot-type convention. None of them is a
table; they exist so the pure functions have well-typed inputs.

### `ObservationSnapshot` (core)

`event_id`, `source`, `observed_at_ms`, `payload: Mapping[str, object]`, `severity: float`.
The input to `core/estimates.py` and `core/duplicates.py`.

### `EvidenceBundle` (real)

`agent_id`, `hypothesis_id`, `hypothesis_statement`, `scope: EvidenceAccessScope`,
`observations: tuple[ObservationSnapshot, ...]`, `built_at_ms`.

**The critical property**: this type has no field that could carry an outcome, a ground-truth label
or an accuracy parameter. FR-113's exclusion is enforced by the type, verified by the recorded
serialization, and asserted by a unit test that walks the dataclass fields. `frozen=True` means an
agent cannot mutate what it was given.

### `EvidenceAccessScope` (real)

`agent_id`, `source_ids: frozenset[str]`, `label`. Declared in configuration; the only input to
bundle construction. A forecast claiming an evidence group outside its scope is rejected and
recorded (FR-116).

### `ForecastProposal` (real)

`agent_id`, `hypothesis_id`, `probability`, `stake`, `evidence_cluster_id`, `rationale`,
`interaction_id`. What a reasoning agent returns; validated before it becomes a `forecasts` row.

### `AttributeEstimate` (core)

`attribute`, `value`, `basis`, `estimator`. Returned by `core/estimates.py`. The `basis` field is
required by the type, so an estimate without a derivation is unrepresentable rather than merely
discouraged.

### `ReplayComparison` (replay)

`identical: bool`, `records_compared: int`, `first_divergence: Divergence | None`,
`model_requests_made: int`.

---

## Entity relationships

```
observation_sources ──emits──> events (mode='real')
                                  │
                                  │ cited by
                                  ▼
model_interactions ──produces──> proposals ──promoted to──> hypotheses
                                                  │              │
                                                  │              ├──> attribute_estimates
                                                  │              │     (written by core/estimates.py,
                                                  │              │      never by model output)
                                                  │              │
                                                  │              ├──> forecasts (evidence_bundle recorded)
                                                  │              │        │
                                                  │              │        └──> settlements
                                                  │              │
                                                  │              └──> resolutions ──writes through──> outcomes
                                                  ▼
                                          proposals.merged_into (D-023)

replay_runs ──compares──> attention_decisions + settlements + agents.available_credits
```

## What this model deliberately does not do

- **No `agent_accuracy` column anywhere.** FR-117 forbids storing or displaying a configured
  accuracy figure in real mode. Calibration is derived from `settlements` on read, and returns
  insufficiency below the minimum resolved count (D-022). `simulator/agents.py::AgentProfile.accuracy`
  remains, scoped entirely to the simulated tenant.
- **No mutable outcome.** `resolutions` is append-only; corrections are new sequence numbers.
- **No unredacted payload column.** There is no "raw" alongside "redacted" — the raw content never
  reaches storage (D-014).
- **No cross-tenant view or index.** Aggregating `hostops` with `acmepay` should require writing new
  code, not merely omitting a filter (D-018, SC-117).
