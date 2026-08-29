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
