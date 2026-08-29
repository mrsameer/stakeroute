"""Transactional SQLite storage.

Everything in this module is plumbing arranged around the pure core (D-001):
it owns the connection, the schema, and every write. The core never imports
this module; this module imports the core's snapshot types to build queries,
never the other way round.
"""

from __future__ import annotations

import functools
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# WAL is the default: concurrent readers don't block on a writer, which is
# what the idempotency argument in contracts/events.md leans on. It relies
# on an mmap'd shared-memory (-shm) file for its locking coordination,
# which some virtualized/networked volume backends handle unreliably
# across separate containers. STAKEROUTE_SQLITE_JOURNAL_MODE=DELETE falls
# back to SQLite's classic rollback-journal locking (readers block briefly
# during a write, but the lock protocol itself is the most portable one
# SQLite has) — set it in docker-compose.yml if WAL misbehaves there.
_JOURNAL_MODE = os.environ.get("STAKEROUTE_SQLITE_JOURNAL_MODE", "WAL")


def _retrying(method):
    """Retry a Repository method on ``sqlite3.OperationalError: database
    is locked``.

    ``PRAGMA busy_timeout`` covers a connection waiting on a lock held
    *within* SQLite's own retry loop, but three separate processes
    (worker, dashboard, simulator — D-008) each opening a fresh connection
    and writing within milliseconds of each other — most visibly at
    container startup, when all three bootstrap the schema and seed their
    first rows at once — can still collide before that loop ever starts.
    This is operational hardening for that race, not a claim that
    concurrent writers scale; the sustained multi-writer limitation is
    already documented at D-004.
    """

    @functools.wraps(method)
    def wrapper(self: Repository, *args, **kwargs):
        attempts = 60
        base_delay = 0.2
        max_delay = 1.0
        for attempt in range(attempts):
            try:
                return method(self, *args, **kwargs)
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == attempts - 1:
                    raise
                time.sleep(min(base_delay * (attempt + 1), max_delay))
        raise AssertionError("unreachable")

    return wrapper


class Repository:
    """A transactional handle onto the StakeRoute SQLite store.

    A single ``sqlite3.Connection`` is not safe for *simultaneous* use from
    more than one thread — ``check_same_thread=False`` only lifts sqlite3's
    same-thread assertion, it does not serialize access. FastAPI runs sync
    endpoints in a threadpool, so two requests (e.g. the dashboard's own
    ``Promise.all`` of four GETs) can legitimately call Repository methods
    at the same instant. Every public method below therefore holds
    ``self._lock`` for its full body, including any read-then-write
    sequence (e.g. ``upsert_forecast``'s delete-then-insert) that must
    itself be atomic against a concurrent caller. ``@_retrying`` adds the
    second layer: retry on a lock held by a *different process* sharing
    the same file (worker/dashboard/simulator, D-008).
    """

    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(f"PRAGMA journal_mode={_JOURNAL_MODE}")
        self._conn.execute("PRAGMA foreign_keys=ON")
        # busy_timeout makes SQLite retry for up to 5s instead of raising
        # immediately when a lock is already held.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._bootstrap_schema()

    # Columns added by feature 002. SQLite's ALTER TABLE ADD COLUMN has no
    # "IF NOT EXISTS" form, so these are applied idempotently in Python —
    # via PRAGMA table_info — rather than as raw ALTER statements in
    # schema.sql, which would fail every run after the first against an
    # already-migrated database file.
    _MODE_COLUMN_TABLES = (
        "events",
        "hypotheses",
        "forecasts",
        "attention_decisions",
        "outcomes",
        "settlements",
    )
    _EXTRA_COLUMNS: dict[str, list[str]] = {
        "hypotheses": [
            "proposal_id TEXT REFERENCES proposals(id)",
            "condition_name TEXT",
            "condition_params TEXT",
        ],
        "forecasts": [
            "evidence_bundle TEXT",
            "rationale TEXT",
            "interaction_id TEXT REFERENCES model_interactions(id)",
        ],
    }

    def _migrate_columns(self) -> None:
        for table in self._MODE_COLUMN_TABLES:
            existing = {
                row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            if "mode" not in existing:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN mode TEXT NOT NULL DEFAULT 'sim'"
                )
        for table, columns in self._EXTRA_COLUMNS.items():
            existing = {
                row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            for column_def in columns:
                column_name = column_def.split()[0]
                if column_name not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")

    @_retrying
    def _bootstrap_schema(self) -> None:
        with self._lock:
            schema = _SCHEMA_PATH.read_text()
            self._conn.executescript(schema)
            self._migrate_columns()
            # Both tenants exist from the first migration onward (D-018):
            # 'acmepay' for the seeded simulation, 'hostops' for real mode.
            # A fixed created_at_ms is fine here — the row's existence is
            # what matters, not when schema init happened to run.
            self._conn.execute(
                "INSERT INTO tenants (id, name, created_at_ms) VALUES "
                "('acmepay', 'AcmePay', 0) ON CONFLICT (id) DO NOTHING"
            )
            self._conn.execute(
                "INSERT INTO tenants (id, name, created_at_ms) VALUES "
                "('hostops', 'Host Operations', 0) ON CONFLICT (id) DO NOTHING"
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- Idempotency boundary ------------------------------------------------

    @_retrying
    def insert_event(
        self,
        event_id: str,
        tenant_id: str,
        source: str,
        source_event_id: str,
        observed_at_ms: int,
        ingested_at_ms: int,
        provenance: dict,
        payload: dict,
        mode: str = "sim",
    ) -> bool:
        """Insert an event, ignoring the write on a duplicate ``event_id``.

        Returns ``True`` if this was a new row (the effect should be
        applied), ``False`` if the event was already recorded (skip the
        effect — this is what makes redelivery safe, FR-003).
        """
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO events (
                    event_id, tenant_id, source, source_event_id,
                    observed_at_ms, ingested_at_ms, provenance, payload, mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    event_id,
                    tenant_id,
                    source,
                    source_event_id,
                    observed_at_ms,
                    ingested_at_ms,
                    json.dumps(provenance),
                    json.dumps(payload),
                    mode,
                ),
            )
            return cursor.rowcount > 0

    @_retrying
    def list_events(
        self, tenant_id: str, since_ms: int = 0, until_ms: int | None = None
    ) -> list[sqlite3.Row]:
        with self._lock:
            if until_ms is None:
                return self._conn.execute(
                    "SELECT * FROM events WHERE tenant_id = ? AND observed_at_ms >= ? "
                    "ORDER BY observed_at_ms",
                    (tenant_id, since_ms),
                ).fetchall()
            return self._conn.execute(
                "SELECT * FROM events WHERE tenant_id = ? AND observed_at_ms >= ? "
                "AND observed_at_ms <= ? ORDER BY observed_at_ms",
                (tenant_id, since_ms, until_ms),
            ).fetchall()

    @_retrying
    def get_event(self, event_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()

    @_retrying
    def events_exist(self, tenant_id: str, event_ids: list[str]) -> set[str]:
        """Return the subset of ``event_ids`` that exist for ``tenant_id``.

        Used to validate proposal citations (FR-122): an unknown id is one
        not in the returned set.
        """
        if not event_ids:
            return set()
        with self._lock:
            placeholders = ",".join("?" for _ in event_ids)
            rows = self._conn.execute(
                f"SELECT event_id FROM events WHERE tenant_id = ? "
                f"AND event_id IN ({placeholders})",
                (tenant_id, *event_ids),
            ).fetchall()
            return {row["event_id"] for row in rows}

    @_retrying
    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()

    # -- Tenants --------------------------------------------------------------

    @_retrying
    def ensure_tenant(self, tenant_id: str, name: str, created_at_ms: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tenants (id, name, created_at_ms) VALUES (?, ?, ?) "
                "ON CONFLICT (id) DO NOTHING",
                (tenant_id, name, created_at_ms),
            )

    # -- Agents -----------------------------------------------------------

    @_retrying
    def upsert_agent(
        self,
        agent_id: str,
        tenant_id: str,
        display_name: str,
        reputation: float,
        available_credits: int,
        staked_credits: int,
        attested: bool,
        created_at_ms: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agents (
                    id, tenant_id, display_name, reputation, available_credits,
                    staked_credits, attested, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    reputation = excluded.reputation,
                    available_credits = excluded.available_credits,
                    staked_credits = excluded.staked_credits
                """,
                (
                    agent_id,
                    tenant_id,
                    display_name,
                    reputation,
                    available_credits,
                    staked_credits,
                    int(attested),
                    created_at_ms,
                ),
            )

    @_retrying
    def get_agent(self, agent_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()

    @_retrying
    def list_agents(self, tenant_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM agents WHERE tenant_id = ? ORDER BY id", (tenant_id,)
            ).fetchall()

    @_retrying
    def adjust_agent_credits(
        self, agent_id: str, available_delta: int, staked_delta: int
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE agents
                SET available_credits = available_credits + ?,
                    staked_credits = staked_credits + ?
                WHERE id = ?
                """,
                (available_delta, staked_delta, agent_id),
            )

    @_retrying
    def set_agent_reputation(self, agent_id: str, reputation: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE agents SET reputation = ? WHERE id = ?", (reputation, agent_id)
            )

    # -- Evidence clusters --------------------------------------------------

    @_retrying
    def ensure_evidence_cluster(
        self, cluster_id: str, tenant_id: str, label: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO evidence_clusters (id, tenant_id, label, member_count)
                VALUES (?, ?, ?, 0)
                ON CONFLICT (id) DO NOTHING
                """,
                (cluster_id, tenant_id, label),
            )

    # -- Hypotheses -----------------------------------------------------------

    @_retrying
    def upsert_hypothesis(
        self,
        hypothesis_id: str,
        tenant_id: str,
        statement: str,
        prior_probability: float,
        impact_minor_units: int,
        urgency: float,
        review_cost: float,
        deadline_ms: int,
        status: str,
        created_at_ms: int,
        mode: str = "sim",
        proposal_id: str | None = None,
        condition_name: str | None = None,
        condition_params: dict | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO hypotheses (
                    id, tenant_id, statement, prior_probability, impact_minor_units,
                    urgency, review_cost, deadline_ms, status, created_at_ms,
                    mode, proposal_id, condition_name, condition_params
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    hypothesis_id,
                    tenant_id,
                    statement,
                    prior_probability,
                    impact_minor_units,
                    urgency,
                    review_cost,
                    deadline_ms,
                    status,
                    created_at_ms,
                    mode,
                    proposal_id,
                    condition_name,
                    json.dumps(condition_params)
                    if condition_params is not None
                    else None,
                ),
            )

    @_retrying
    def set_hypothesis_status(self, hypothesis_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE hypotheses SET status = ? WHERE id = ?", (status, hypothesis_id)
            )

    @_retrying
    def get_hypothesis(self, hypothesis_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)
            ).fetchone()

    @_retrying
    def list_hypotheses(
        self, tenant_id: str, status: str | None = None
    ) -> list[sqlite3.Row]:
        with self._lock:
            if status is None:
                return self._conn.execute(
                    "SELECT * FROM hypotheses WHERE tenant_id = ? ORDER BY id",
                    (tenant_id,),
                ).fetchall()
            return self._conn.execute(
                "SELECT * FROM hypotheses WHERE tenant_id = ? AND status = ? "
                "ORDER BY id",
                (tenant_id, status),
            ).fetchall()

    @_retrying
    def list_expired_hypotheses(self, tenant_id: str, now_ms: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                """
                SELECT * FROM hypotheses
                WHERE tenant_id = ? AND status = 'open' AND deadline_ms < ?
                ORDER BY id
                """,
                (tenant_id, now_ms),
            ).fetchall()

    # -- Forecasts --------------------------------------------------------

    @_retrying
    def get_live_forecast(
        self, hypothesis_id: str, agent_id: str
    ) -> sqlite3.Row | None:
        """Look up an agent's current live forecast on a hypothesis, if any.

        Used to validate a resubmission against the *net* stake delta
        rather than the raw available balance (FR-044) — replacing a
        forecast at the same stake must not be rejected just because the
        original charge already left the balance low.
        """
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM forecasts WHERE hypothesis_id = ? AND agent_id = ?",
                (hypothesis_id, agent_id),
            ).fetchone()

    @_retrying
    def upsert_forecast(
        self,
        forecast_id: str,
        tenant_id: str,
        hypothesis_id: str,
        agent_id: str,
        probability: float,
        stake: int,
        evidence_cluster_id: str,
        evidence_refs: list[str],
        source_event_id: str,
        created_at_ms: int,
        expires_at_ms: int,
        mode: str = "sim",
        evidence_bundle: dict | None = None,
        rationale: str | None = None,
        interaction_id: str | None = None,
    ) -> str | None:
        """Insert or replace an agent's live forecast on a hypothesis.

        Only one live forecast per ``(hypothesis_id, agent_id)`` is allowed
        (FR-044); a resubmission replaces the prior row. Returns the
        previous forecast's id and stake tuple-encoded as a string ("id:stake")
        if one existed, else ``None`` — the caller uses this to reconcile the
        stake difference against the agent's balance.

        The lookup, delete and insert below run under one lock acquisition
        so a concurrent caller can never observe (or race) a half-replaced
        forecast.
        """
        with self._lock:
            existing = self._conn.execute(
                "SELECT id, stake FROM forecasts "
                "WHERE hypothesis_id = ? AND agent_id = ?",
                (hypothesis_id, agent_id),
            ).fetchone()
            if existing is not None:
                self._conn.execute(
                    "DELETE FROM forecasts WHERE id = ?", (existing["id"],)
                )
            self._conn.execute(
                """
                INSERT INTO forecasts (
                    id, tenant_id, hypothesis_id, agent_id, probability, stake,
                    evidence_cluster_id, evidence_refs, source_event_id,
                    created_at_ms, expires_at_ms, mode, evidence_bundle,
                    rationale, interaction_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    forecast_id,
                    tenant_id,
                    hypothesis_id,
                    agent_id,
                    probability,
                    stake,
                    evidence_cluster_id,
                    json.dumps(evidence_refs),
                    source_event_id,
                    created_at_ms,
                    expires_at_ms,
                    mode,
                    json.dumps(evidence_bundle)
                    if evidence_bundle is not None
                    else None,
                    rationale,
                    interaction_id,
                ),
            )
            if existing is not None:
                return f"{existing['id']}:{existing['stake']}"
            return None

    @_retrying
    def list_forecasts_for_hypothesis(self, hypothesis_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM forecasts WHERE hypothesis_id = ? ORDER BY agent_id",
                (hypothesis_id,),
            ).fetchall()

    @_retrying
    def get_forecast(self, forecast_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM forecasts WHERE id = ?", (forecast_id,)
            ).fetchone()

    @_retrying
    def delete_forecasts_for_hypothesis(self, hypothesis_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM forecasts WHERE hypothesis_id = ?", (hypothesis_id,)
            )

    # -- Attention decisions --------------------------------------------------

    @_retrying
    def insert_attention_decision(
        self,
        tenant_id: str,
        hypothesis_id: str,
        strategy: str,
        aggregated_probability: float,
        priority: float,
        rank: int,
        routed: bool,
        reason: str,
        contributions: list[dict],
        decided_at_ms: int,
        mode: str = "sim",
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO attention_decisions (
                    tenant_id, hypothesis_id, strategy, aggregated_probability,
                    priority, rank, routed, reason, contributions, decided_at_ms,
                    mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    hypothesis_id,
                    strategy,
                    aggregated_probability,
                    priority,
                    rank,
                    int(routed),
                    reason,
                    json.dumps(contributions),
                    decided_at_ms,
                    mode,
                ),
            )

    @_retrying
    def latest_decisions(self, tenant_id: str, strategy: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                """
                SELECT ad.* FROM attention_decisions ad
                INNER JOIN (
                    SELECT hypothesis_id, MAX(decided_at_ms) AS max_ms
                    FROM attention_decisions
                    WHERE tenant_id = ? AND strategy = ?
                    GROUP BY hypothesis_id
                ) latest
                ON ad.hypothesis_id = latest.hypothesis_id
                AND ad.decided_at_ms = latest.max_ms
                WHERE ad.tenant_id = ? AND ad.strategy = ?
                ORDER BY ad.rank
                """,
                (tenant_id, strategy, tenant_id, strategy),
            ).fetchall()

    @_retrying
    def count_events(self, tenant_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM events WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
            return int(row["n"])

    # -- Outcomes and settlements ---------------------------------------------

    @_retrying
    def insert_outcome(
        self,
        hypothesis_id: str,
        tenant_id: str,
        outcome: int,
        resolved_at_ms: int,
        resolved_by: str,
        mode: str = "sim",
    ) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO outcomes (
                    hypothesis_id, tenant_id, outcome, resolved_at_ms, resolved_by,
                    mode
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (hypothesis_id) DO NOTHING
                """,
                (hypothesis_id, tenant_id, outcome, resolved_at_ms, resolved_by, mode),
            )
            return cursor.rowcount > 0

    @_retrying
    def get_outcome(self, hypothesis_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM outcomes WHERE hypothesis_id = ?", (hypothesis_id,)
            ).fetchone()

    @_retrying
    def insert_settlement(
        self,
        tenant_id: str,
        forecast_id: str,
        brier_score: float,
        prior_brier_score: float,
        improvement: float,
        credit_delta: int,
        reputation_before: float,
        reputation_after: float,
        settled_at_ms: int,
        mode: str = "sim",
    ) -> bool:
        """Insert a settlement, ignoring a duplicate ``forecast_id``.

        Returns ``True`` if this was a new row — the second half of the
        idempotency guarantee alongside ``insert_event`` (FR-003, SC-005).
        """
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO settlements (
                    tenant_id, forecast_id, brier_score, prior_brier_score,
                    improvement, credit_delta, reputation_before, reputation_after,
                    settled_at_ms, mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (forecast_id) DO NOTHING
                """,
                (
                    tenant_id,
                    forecast_id,
                    brier_score,
                    prior_brier_score,
                    improvement,
                    credit_delta,
                    reputation_before,
                    reputation_after,
                    settled_at_ms,
                    mode,
                ),
            )
            return cursor.rowcount > 0

    @_retrying
    def list_settlements_for_hypothesis(self, hypothesis_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                """
                SELECT s.* FROM settlements s
                INNER JOIN forecasts f ON f.id = s.forecast_id
                WHERE f.hypothesis_id = ?
                ORDER BY s.forecast_id
                """,
                (hypothesis_id,),
            ).fetchall()

    @_retrying
    def get_last_forecast_probability(self, agent_id: str) -> float | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT probability FROM forecasts WHERE agent_id = ? "
                "ORDER BY created_at_ms DESC LIMIT 1",
                (agent_id,),
            ).fetchone()
            return row["probability"] if row else None

    @_retrying
    def get_last_settlement_delta(self, agent_id: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT s.credit_delta FROM settlements s
                INNER JOIN forecasts f ON f.id = s.forecast_id
                WHERE f.agent_id = ?
                ORDER BY s.settled_at_ms DESC LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
            return row["credit_delta"] if row else None

    @_retrying
    def duplicate_settlement_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS n FROM (
                    SELECT forecast_id FROM settlements
                    GROUP BY forecast_id HAVING COUNT(*) > 1
                )
                """
            ).fetchone()
            return int(row["n"])

    # -- Metrics support ------------------------------------------------------

    @_retrying
    def list_all_settlements(self, tenant_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM settlements WHERE tenant_id = ? ORDER BY id",
                (tenant_id,),
            ).fetchall()

    @_retrying
    def event_ingestion_span_ms(self, tenant_id: str) -> tuple[int, int, int] | None:
        """Return ``(count, earliest_ingested_at_ms, latest_ingested_at_ms)``
        for ``tenant_id``, or ``None`` if no events are recorded yet."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS n, MIN(ingested_at_ms) AS lo,
                       MAX(ingested_at_ms) AS hi
                FROM events WHERE tenant_id = ?
                """,
                (tenant_id,),
            ).fetchone()
            if row is None or row["n"] == 0:
                return None
            return int(row["n"]), int(row["lo"]), int(row["hi"])

    @_retrying
    def newest_event_ingested_at_ms(self, tenant_id: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(ingested_at_ms) AS latest FROM events WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            return int(row["latest"]) if row and row["latest"] is not None else None

    @_retrying
    def newest_decision_ms(self, tenant_id: str, strategy: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(decided_at_ms) AS latest FROM attention_decisions "
                "WHERE tenant_id = ? AND strategy = ?",
                (tenant_id, strategy),
            ).fetchone()
            return int(row["latest"]) if row and row["latest"] is not None else None

    # -- Epochs -----------------------------------------------------------

    @_retrying
    def insert_epoch(
        self,
        tenant_id: str,
        started_at_ms: int,
        grant_per_agent: int,
        seed: int,
    ) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO epochs (tenant_id, started_at_ms, grant_per_agent, seed)
                VALUES (?, ?, ?, ?)
                """,
                (tenant_id, started_at_ms, grant_per_agent, seed),
            )
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    # -- Reset (demo scenario control) ---------------------------------------

    @_retrying
    def reset_tenant(self, tenant_id: str) -> None:
        """Clear every tenant-scoped row so a scenario can restart clean.

        Used by ``POST /api/scenario/run_normal`` — the tenant row itself
        and the schema are untouched.
        """
        with self._lock:
            for table in (
                "settlements",
                "outcomes",
                "attention_decisions",
                "rejected_forecasts",
                "forecasts",
                "hypotheses",
                "evidence_clusters",
                "agents",
                "events",
                "epochs",
            ):
                self._conn.execute(
                    f"DELETE FROM {table} WHERE tenant_id = ?", (tenant_id,)
                )
            self._conn.commit()

    # -- Rejected forecasts (FR-008) -----------------------------------------

    @_retrying
    def insert_rejected_forecast(
        self,
        tenant_id: str,
        hypothesis_id: str,
        agent_id: str,
        stake: int,
        probability: float,
        reason: str,
        rejected_at_ms: int,
    ) -> None:
        """Record a forecast rejection explicitly rather than dropping it.

        FR-008 requires a rejection to be recorded, not silently discarded
        — the operator (and the adversarial test suite) must be able to see
        that a submission was refused and why.
        """
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO rejected_forecasts (
                    tenant_id, hypothesis_id, agent_id, stake, probability,
                    reason, rejected_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    hypothesis_id,
                    agent_id,
                    stake,
                    probability,
                    reason,
                    rejected_at_ms,
                ),
            )

    @_retrying
    def list_rejected_forecasts(self, tenant_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM rejected_forecasts WHERE tenant_id = ? ORDER BY id",
                (tenant_id,),
            ).fetchall()

    # -- Observation sources (FR-141) -----------------------------------------

    @_retrying
    def upsert_observation_source(
        self,
        source_id: str,
        tenant_id: str,
        display_name: str,
        state: str,
        last_seen_ms: int | None,
        silence_threshold_ms: int,
        absent_reason: str | None,
        set_aside_count: int,
        updated_at_ms: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO observation_sources (
                    id, tenant_id, display_name, state, last_seen_ms,
                    silence_threshold_ms, absent_reason, set_aside_count,
                    updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    state = excluded.state,
                    last_seen_ms = excluded.last_seen_ms,
                    absent_reason = excluded.absent_reason,
                    set_aside_count = excluded.set_aside_count,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    source_id,
                    tenant_id,
                    display_name,
                    state,
                    last_seen_ms,
                    silence_threshold_ms,
                    absent_reason,
                    set_aside_count,
                    updated_at_ms,
                ),
            )

    @_retrying
    def get_observation_source(self, source_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM observation_sources WHERE id = ?", (source_id,)
            ).fetchone()

    @_retrying
    def list_observation_sources(self, tenant_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM observation_sources WHERE tenant_id = ? ORDER BY id",
                (tenant_id,),
            ).fetchall()

    # -- Model interactions (FR-123) -------------------------------------------

    @_retrying
    def insert_model_interaction(
        self,
        interaction_id: str,
        tenant_id: str,
        mode: str,
        purpose: str,
        agent_id: str | None,
        request: str,
        response: str | None,
        latency_ms: int,
        accepted: bool,
        rejection_reason: str | None,
        model_name: str,
        requested_at_ms: int,
    ) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO model_interactions (
                    id, tenant_id, mode, purpose, agent_id, request, response,
                    latency_ms, accepted, rejection_reason, model_name,
                    requested_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    interaction_id,
                    tenant_id,
                    mode,
                    purpose,
                    agent_id,
                    request,
                    response,
                    latency_ms,
                    int(accepted),
                    rejection_reason,
                    model_name,
                    requested_at_ms,
                ),
            )
            return cursor.rowcount > 0

    @_retrying
    def get_model_interaction(self, interaction_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM model_interactions WHERE id = ?", (interaction_id,)
            ).fetchone()

    @_retrying
    def list_model_interactions(
        self, tenant_id: str, since_ms: int = 0
    ) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM model_interactions WHERE tenant_id = ? "
                "AND requested_at_ms >= ? ORDER BY requested_at_ms",
                (tenant_id, since_ms),
            ).fetchall()

    # -- Proposals (FR-107, FR-111) --------------------------------------------

    @_retrying
    def insert_proposal(
        self,
        proposal_id: str,
        tenant_id: str,
        mode: str,
        statement: str,
        cited_observation_ids: list[str],
        condition_name: str | None,
        condition_params: dict | None,
        interaction_id: str,
        status: str,
        created_at_ms: int,
        merged_into: str | None = None,
        rejection_reason: str | None = None,
    ) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO proposals (
                    id, tenant_id, mode, statement, cited_observation_ids,
                    condition_name, condition_params, interaction_id, status,
                    merged_into, rejection_reason, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    proposal_id,
                    tenant_id,
                    mode,
                    statement,
                    json.dumps(cited_observation_ids),
                    condition_name,
                    json.dumps(condition_params)
                    if condition_params is not None
                    else None,
                    interaction_id,
                    status,
                    merged_into,
                    rejection_reason,
                    created_at_ms,
                ),
            )
            return cursor.rowcount > 0

    @_retrying
    def get_proposal(self, proposal_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()

    @_retrying
    def list_proposals(
        self, tenant_id: str, status: str | None = None
    ) -> list[sqlite3.Row]:
        with self._lock:
            if status is None:
                return self._conn.execute(
                    "SELECT * FROM proposals WHERE tenant_id = ? "
                    "ORDER BY created_at_ms",
                    (tenant_id,),
                ).fetchall()
            return self._conn.execute(
                "SELECT * FROM proposals WHERE tenant_id = ? AND status = ? "
                "ORDER BY created_at_ms",
                (tenant_id, status),
            ).fetchall()

    @_retrying
    def set_proposal_status(
        self,
        proposal_id: str,
        status: str,
        merged_into: str | None = None,
        rejection_reason: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE proposals SET status = ?, merged_into = ?, "
                "rejection_reason = ? WHERE id = ?",
                (status, merged_into, rejection_reason, proposal_id),
            )

    # -- Attribute estimates (FR-108, FR-109) ----------------------------------

    @_retrying
    def insert_attribute_estimate(
        self,
        tenant_id: str,
        hypothesis_id: str,
        attribute: str,
        value: float,
        basis: str,
        estimator: str,
        confirmed_by_operator: bool,
        confirmed_at_ms: int | None,
        created_at_ms: int,
    ) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO attribute_estimates (
                    tenant_id, hypothesis_id, attribute, value, basis, estimator,
                    confirmed_by_operator, confirmed_at_ms, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    hypothesis_id,
                    attribute,
                    value,
                    basis,
                    estimator,
                    int(confirmed_by_operator),
                    confirmed_at_ms,
                    created_at_ms,
                ),
            )
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    @_retrying
    def supersede_attribute_estimate(self, old_id: int, new_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE attribute_estimates SET superseded_by = ? WHERE id = ?",
                (new_id, old_id),
            )

    @_retrying
    def list_attribute_estimates(self, hypothesis_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM attribute_estimates WHERE hypothesis_id = ? "
                "ORDER BY attribute, created_at_ms",
                (hypothesis_id,),
            ).fetchall()

    @_retrying
    def current_attribute_estimates(self, hypothesis_id: str) -> list[sqlite3.Row]:
        """The live (non-superseded) estimate for each attribute."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM attribute_estimates WHERE hypothesis_id = ? "
                "AND superseded_by IS NULL ORDER BY attribute",
                (hypothesis_id,),
            ).fetchall()

    # -- Resolutions (FR-133, FR-135, FR-136, FR-148) --------------------------

    @_retrying
    def insert_resolution(
        self,
        tenant_id: str,
        hypothesis_id: str,
        resolution_seq: int,
        outcome: int,
        determination: str,
        source: str,
        check_name: str | None,
        check_params: dict | None,
        check_result: str | None,
        checked_at_ms: int | None,
        arrived_at_ms: int,
        settled: bool,
        not_settled_reason: str | None,
        dedup_key: str,
    ) -> bool:
        """Insert a resolution, ignoring a duplicate ``dedup_key``.

        Returns ``True`` if this was a new row — a redelivered outcome
        inserts zero rows and produces exactly one settlement effect
        (FR-135, Principle III).
        """
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO resolutions (
                    tenant_id, hypothesis_id, resolution_seq, outcome,
                    determination, source, check_name, check_params,
                    check_result, checked_at_ms, arrived_at_ms, settled,
                    not_settled_reason, dedup_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (dedup_key) DO NOTHING
                """,
                (
                    tenant_id,
                    hypothesis_id,
                    resolution_seq,
                    outcome,
                    determination,
                    source,
                    check_name,
                    json.dumps(check_params) if check_params is not None else None,
                    check_result,
                    checked_at_ms,
                    arrived_at_ms,
                    int(settled),
                    not_settled_reason,
                    dedup_key,
                ),
            )
            return cursor.rowcount > 0

    @_retrying
    def list_resolutions_for_hypothesis(self, hypothesis_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM resolutions WHERE hypothesis_id = ? "
                "ORDER BY resolution_seq",
                (hypothesis_id,),
            ).fetchall()

    @_retrying
    def latest_resolution(self, hypothesis_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM resolutions WHERE hypothesis_id = ? "
                "ORDER BY resolution_seq DESC LIMIT 1",
                (hypothesis_id,),
            ).fetchone()

    # -- Replay runs (FR-129 – FR-131) ------------------------------------------

    @_retrying
    def insert_replay_run(
        self,
        replay_run_id: str,
        tenant_id: str,
        source_window_start_ms: int,
        source_window_end_ms: int,
        started_at_ms: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO replay_runs (
                    id, tenant_id, source_window_start_ms, source_window_end_ms,
                    started_at_ms, completed_at_ms, identical, first_divergence,
                    records_compared, model_requests_made
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, 0, 0)
                """,
                (
                    replay_run_id,
                    tenant_id,
                    source_window_start_ms,
                    source_window_end_ms,
                    started_at_ms,
                ),
            )

    @_retrying
    def complete_replay_run(
        self,
        replay_run_id: str,
        completed_at_ms: int,
        identical: bool,
        first_divergence: dict | None,
        records_compared: int,
        model_requests_made: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE replay_runs SET
                    completed_at_ms = ?, identical = ?, first_divergence = ?,
                    records_compared = ?, model_requests_made = ?
                WHERE id = ?
                """,
                (
                    completed_at_ms,
                    int(identical),
                    json.dumps(first_divergence)
                    if first_divergence is not None
                    else None,
                    records_compared,
                    model_requests_made,
                    replay_run_id,
                ),
            )

    @_retrying
    def get_replay_run(self, replay_run_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM replay_runs WHERE id = ?", (replay_run_id,)
            ).fetchone()
