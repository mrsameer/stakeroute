-- StakeRoute schema. Postgres-portable DDL only (D-004): no SQLite-specific
-- syntax beyond AUTOINCREMENT-free INTEGER PRIMARY KEY usage, which both
-- engines accept for surrogate keys defined explicitly below.
--
-- tenant_id is present on every table from the first migration, even in a
-- single-tenant demo (Constitution, Additional Constraints).

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    display_name TEXT NOT NULL,
    reputation REAL NOT NULL,
    available_credits INTEGER NOT NULL,
    staked_credits INTEGER NOT NULL,
    attested INTEGER NOT NULL,
    created_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agents_tenant ON agents(tenant_id);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    source TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    observed_at_ms INTEGER NOT NULL,
    ingested_at_ms INTEGER NOT NULL,
    provenance TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_tenant_observed
    ON events(tenant_id, observed_at_ms);

CREATE TABLE IF NOT EXISTS evidence_clusters (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    label TEXT NOT NULL,
    member_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    statement TEXT NOT NULL,
    prior_probability REAL NOT NULL,
    impact_minor_units INTEGER NOT NULL,
    urgency REAL NOT NULL,
    review_cost REAL NOT NULL,
    deadline_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hypotheses_tenant_status
    ON hypotheses(tenant_id, status);

CREATE TABLE IF NOT EXISTS forecasts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id),
    agent_id TEXT NOT NULL REFERENCES agents(id),
    probability REAL NOT NULL,
    stake INTEGER NOT NULL,
    evidence_cluster_id TEXT NOT NULL REFERENCES evidence_clusters(id),
    evidence_refs TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER NOT NULL,
    UNIQUE (hypothesis_id, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_forecasts_hypothesis ON forecasts(hypothesis_id);

CREATE TABLE IF NOT EXISTS attention_decisions (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id),
    strategy TEXT NOT NULL,
    aggregated_probability REAL NOT NULL,
    priority REAL NOT NULL,
    rank INTEGER NOT NULL,
    routed INTEGER NOT NULL,
    reason TEXT NOT NULL,
    contributions TEXT NOT NULL,
    decided_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attention_decisions_hyp_strategy
    ON attention_decisions(hypothesis_id, strategy, decided_at_ms);

CREATE TABLE IF NOT EXISTS outcomes (
    hypothesis_id TEXT PRIMARY KEY REFERENCES hypotheses(id),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    outcome INTEGER NOT NULL,
    resolved_at_ms INTEGER NOT NULL,
    resolved_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settlements (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    forecast_id TEXT NOT NULL REFERENCES forecasts(id),
    brier_score REAL NOT NULL,
    prior_brier_score REAL NOT NULL,
    improvement REAL NOT NULL,
    credit_delta INTEGER NOT NULL,
    reputation_before REAL NOT NULL,
    reputation_after REAL NOT NULL,
    settled_at_ms INTEGER NOT NULL,
    UNIQUE (forecast_id)
);

CREATE TABLE IF NOT EXISTS epochs (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    started_at_ms INTEGER NOT NULL,
    grant_per_agent INTEGER NOT NULL,
    seed INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rejected_forecasts (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    hypothesis_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    stake INTEGER NOT NULL,
    probability REAL NOT NULL,
    reason TEXT NOT NULL,
    rejected_at_ms INTEGER NOT NULL
);

-- === Real system mode (feature 002) additions below =======================
--
-- ``mode`` and the other feature-002 columns on events/hypotheses/forecasts/
-- attention_decisions/outcomes/settlements are added by
-- Repository._migrate_columns (storage/repository.py), not here — SQLite's
-- ALTER TABLE ADD COLUMN has no "IF NOT EXISTS" form, so a raw ALTER here
-- would fail every time this script re-runs against an already-migrated
-- database. Defaulting ``mode`` to 'sim' is what keeps every feature-001
-- row and query path unchanged (D-019, SC-108). No existing query filters
-- on it.

-- A source is a record, so silence is representable (FR-141).
CREATE TABLE IF NOT EXISTS observation_sources (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    display_name TEXT NOT NULL,
    state TEXT NOT NULL,
    last_seen_ms INTEGER,
    silence_threshold_ms INTEGER NOT NULL,
    absent_reason TEXT,
    set_aside_count INTEGER NOT NULL DEFAULT 0,
    updated_at_ms INTEGER NOT NULL,
    CHECK (state IN ('live', 'quiet', 'silent', 'absent')),
    CHECK (state != 'absent' OR absent_reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_observation_sources_tenant
    ON observation_sources(tenant_id);

-- The audit object that makes the model boundary inspectable (FR-123).
CREATE TABLE IF NOT EXISTS model_interactions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    mode TEXT NOT NULL,
    purpose TEXT NOT NULL,
    agent_id TEXT,
    request TEXT NOT NULL,
    response TEXT,
    latency_ms INTEGER NOT NULL,
    accepted INTEGER NOT NULL,
    rejection_reason TEXT,
    model_name TEXT NOT NULL,
    requested_at_ms INTEGER NOT NULL,
    CHECK (accepted IN (0, 1)),
    CHECK (accepted != 0 OR rejection_reason IS NOT NULL),
    CHECK (accepted != 1 OR response IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_model_interactions_tenant_time
    ON model_interactions(tenant_id, requested_at_ms);

-- A candidate hypothesis before it is priced (D-016).
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    mode TEXT NOT NULL,
    statement TEXT NOT NULL,
    cited_observation_ids TEXT NOT NULL,
    condition_name TEXT,
    condition_params TEXT,
    interaction_id TEXT NOT NULL REFERENCES model_interactions(id),
    status TEXT NOT NULL,
    merged_into TEXT REFERENCES proposals(id),
    rejection_reason TEXT,
    created_at_ms INTEGER NOT NULL,
    CHECK (status IN ('pending', 'promoted', 'rejected', 'merged')),
    CHECK (status != 'merged' OR merged_into IS NOT NULL),
    CHECK (status != 'rejected' OR rejection_reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_proposals_tenant_status
    ON proposals(tenant_id, status);

-- The number and the basis that produced it (FR-108, FR-109).
CREATE TABLE IF NOT EXISTS attribute_estimates (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id),
    attribute TEXT NOT NULL,
    value REAL NOT NULL,
    basis TEXT NOT NULL,
    estimator TEXT NOT NULL,
    confirmed_by_operator INTEGER NOT NULL,
    confirmed_at_ms INTEGER,
    superseded_by INTEGER REFERENCES attribute_estimates(id),
    created_at_ms INTEGER NOT NULL,
    UNIQUE (hypothesis_id, attribute, created_at_ms),
    CHECK (confirmed_by_operator IN (0, 1)),
    CHECK (confirmed_by_operator != 1 OR confirmed_at_ms IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_attribute_estimates_hypothesis
    ON attribute_estimates(hypothesis_id, attribute);

-- Outcomes arriving from outside, including corrections (FR-135, FR-136).
CREATE TABLE IF NOT EXISTS resolutions (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id),
    resolution_seq INTEGER NOT NULL,
    outcome INTEGER NOT NULL,
    determination TEXT NOT NULL,
    source TEXT NOT NULL,
    check_name TEXT,
    check_params TEXT,
    check_result TEXT,
    checked_at_ms INTEGER,
    arrived_at_ms INTEGER NOT NULL,
    settled INTEGER NOT NULL,
    not_settled_reason TEXT,
    dedup_key TEXT NOT NULL,
    UNIQUE (dedup_key),
    UNIQUE (hypothesis_id, resolution_seq),
    CHECK (determination IN ('automatic', 'operator')),
    CHECK (
        determination != 'automatic'
        OR (check_name IS NOT NULL AND check_result IS NOT NULL
            AND checked_at_ms IS NOT NULL)
    ),
    CHECK (settled IN (0, 1)),
    CHECK (settled != 0 OR not_settled_reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_resolutions_hypothesis
    ON resolutions(hypothesis_id);

-- The divergence report as a stored fact (FR-131).
CREATE TABLE IF NOT EXISTS replay_runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    source_window_start_ms INTEGER NOT NULL,
    source_window_end_ms INTEGER NOT NULL,
    started_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    identical INTEGER,
    first_divergence TEXT,
    records_compared INTEGER NOT NULL,
    model_requests_made INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_replay_runs_tenant
    ON replay_runs(tenant_id, started_at_ms);
