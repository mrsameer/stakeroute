"""Transactional SQLite storage.

Everything in this module is plumbing arranged around the pure core (D-001):
it owns the connection, the schema, and every write. The core never imports
this module; this module imports the core's snapshot types to build queries,
never the other way round.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class Repository:
    """A transactional handle onto the StakeRoute SQLite store."""

    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: FastAPI runs sync endpoints in a
        # threadpool while async endpoints run on the event loop thread, so
        # the one shared Repository connection is legitimately used from
        # more than one thread. Access is still effectively serialized —
        # nothing here issues concurrent writes from separate threads.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._bootstrap_schema()

    def _bootstrap_schema(self) -> None:
        schema = _SCHEMA_PATH.read_text()
        self._conn.executescript(schema)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- Idempotency boundary ------------------------------------------------

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
    ) -> bool:
        """Insert an event, ignoring the write on a duplicate ``event_id``.

        Returns ``True`` if this was a new row (the effect should be
        applied), ``False`` if the event was already recorded (skip the
        effect — this is what makes redelivery safe, FR-003).
        """
        cursor = self._conn.execute(
            """
            INSERT INTO events (
                event_id, tenant_id, source, source_event_id,
                observed_at_ms, ingested_at_ms, provenance, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        return cursor.rowcount > 0

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    # -- Tenants --------------------------------------------------------------

    def ensure_tenant(self, tenant_id: str, name: str, created_at_ms: int) -> None:
        self._conn.execute(
            "INSERT INTO tenants (id, name, created_at_ms) VALUES (?, ?, ?) "
            "ON CONFLICT (id) DO NOTHING",
            (tenant_id, name, created_at_ms),
        )

    # -- Agents -----------------------------------------------------------

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

    def get_agent(self, agent_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()

    def list_agents(self, tenant_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM agents WHERE tenant_id = ? ORDER BY id", (tenant_id,)
        ).fetchall()

    def adjust_agent_credits(
        self, agent_id: str, available_delta: int, staked_delta: int
    ) -> None:
        self._conn.execute(
            """
            UPDATE agents
            SET available_credits = available_credits + ?,
                staked_credits = staked_credits + ?
            WHERE id = ?
            """,
            (available_delta, staked_delta, agent_id),
        )

    def set_agent_reputation(self, agent_id: str, reputation: float) -> None:
        self._conn.execute(
            "UPDATE agents SET reputation = ? WHERE id = ?", (reputation, agent_id)
        )

    # -- Evidence clusters --------------------------------------------------

    def ensure_evidence_cluster(
        self, cluster_id: str, tenant_id: str, label: str
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO evidence_clusters (id, tenant_id, label, member_count)
            VALUES (?, ?, ?, 0)
            ON CONFLICT (id) DO NOTHING
            """,
            (cluster_id, tenant_id, label),
        )

    # -- Hypotheses -----------------------------------------------------------

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
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO hypotheses (
                id, tenant_id, statement, prior_probability, impact_minor_units,
                urgency, review_cost, deadline_ms, status, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )

    def set_hypothesis_status(self, hypothesis_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE hypotheses SET status = ? WHERE id = ?", (status, hypothesis_id)
        )

    def get_hypothesis(self, hypothesis_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)
        ).fetchone()

    def list_hypotheses(
        self, tenant_id: str, status: str | None = None
    ) -> list[sqlite3.Row]:
        if status is None:
            return self._conn.execute(
                "SELECT * FROM hypotheses WHERE tenant_id = ? ORDER BY id",
                (tenant_id,),
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM hypotheses WHERE tenant_id = ? AND status = ? ORDER BY id",
            (tenant_id, status),
        ).fetchall()

    def list_expired_hypotheses(self, tenant_id: str, now_ms: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT * FROM hypotheses
            WHERE tenant_id = ? AND status = 'open' AND deadline_ms < ?
            ORDER BY id
            """,
            (tenant_id, now_ms),
        ).fetchall()

    # -- Forecasts --------------------------------------------------------

    def get_live_forecast(
        self, hypothesis_id: str, agent_id: str
    ) -> sqlite3.Row | None:
        """Look up an agent's current live forecast on a hypothesis, if any.

        Used to validate a resubmission against the *net* stake delta
        rather than the raw available balance (FR-044) — replacing a
        forecast at the same stake must not be rejected just because the
        original charge already left the balance low.
        """
        return self._conn.execute(
            "SELECT * FROM forecasts WHERE hypothesis_id = ? AND agent_id = ?",
            (hypothesis_id, agent_id),
        ).fetchone()

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
    ) -> str | None:
        """Insert or replace an agent's live forecast on a hypothesis.

        Only one live forecast per ``(hypothesis_id, agent_id)`` is allowed
        (FR-044); a resubmission replaces the prior row. Returns the
        previous forecast's id and stake tuple-encoded as a string ("id:stake")
        if one existed, else ``None`` — the caller uses this to reconcile the
        stake difference against the agent's balance.
        """
        existing = self._conn.execute(
            "SELECT id, stake FROM forecasts WHERE hypothesis_id = ? AND agent_id = ?",
            (hypothesis_id, agent_id),
        ).fetchone()
        if existing is not None:
            self._conn.execute("DELETE FROM forecasts WHERE id = ?", (existing["id"],))
        self._conn.execute(
            """
            INSERT INTO forecasts (
                id, tenant_id, hypothesis_id, agent_id, probability, stake,
                evidence_cluster_id, evidence_refs, source_event_id,
                created_at_ms, expires_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        if existing is not None:
            return f"{existing['id']}:{existing['stake']}"
        return None

    def list_forecasts_for_hypothesis(self, hypothesis_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM forecasts WHERE hypothesis_id = ? ORDER BY agent_id",
            (hypothesis_id,),
        ).fetchall()

    def get_forecast(self, forecast_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM forecasts WHERE id = ?", (forecast_id,)
        ).fetchone()

    def delete_forecasts_for_hypothesis(self, hypothesis_id: str) -> None:
        self._conn.execute(
            "DELETE FROM forecasts WHERE hypothesis_id = ?", (hypothesis_id,)
        )

    # -- Attention decisions --------------------------------------------------

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
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO attention_decisions (
                tenant_id, hypothesis_id, strategy, aggregated_probability,
                priority, rank, routed, reason, contributions, decided_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )

    def latest_decisions(self, tenant_id: str, strategy: str) -> list[sqlite3.Row]:
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

    def count_events(self, tenant_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        return int(row["n"])

    # -- Outcomes and settlements ---------------------------------------------

    def insert_outcome(
        self,
        hypothesis_id: str,
        tenant_id: str,
        outcome: int,
        resolved_at_ms: int,
        resolved_by: str,
    ) -> bool:
        cursor = self._conn.execute(
            """
            INSERT INTO outcomes (
                hypothesis_id, tenant_id, outcome, resolved_at_ms, resolved_by
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (hypothesis_id) DO NOTHING
            """,
            (hypothesis_id, tenant_id, outcome, resolved_at_ms, resolved_by),
        )
        return cursor.rowcount > 0

    def get_outcome(self, hypothesis_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM outcomes WHERE hypothesis_id = ?", (hypothesis_id,)
        ).fetchone()

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
    ) -> bool:
        """Insert a settlement, ignoring a duplicate ``forecast_id``.

        Returns ``True`` if this was a new row — the second half of the
        idempotency guarantee alongside ``insert_event`` (FR-003, SC-005).
        """
        cursor = self._conn.execute(
            """
            INSERT INTO settlements (
                tenant_id, forecast_id, brier_score, prior_brier_score,
                improvement, credit_delta, reputation_before, reputation_after,
                settled_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        return cursor.rowcount > 0

    def list_settlements_for_hypothesis(self, hypothesis_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT s.* FROM settlements s
            INNER JOIN forecasts f ON f.id = s.forecast_id
            WHERE f.hypothesis_id = ?
            ORDER BY s.forecast_id
            """,
            (hypothesis_id,),
        ).fetchall()

    def get_last_forecast_probability(self, agent_id: str) -> float | None:
        row = self._conn.execute(
            "SELECT probability FROM forecasts WHERE agent_id = ? "
            "ORDER BY created_at_ms DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
        return row["probability"] if row else None

    def get_last_settlement_delta(self, agent_id: str) -> int | None:
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

    def duplicate_settlement_count(self) -> int:
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

    def list_all_settlements(self, tenant_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM settlements WHERE tenant_id = ? ORDER BY id", (tenant_id,)
        ).fetchall()

    def event_ingestion_span_ms(self, tenant_id: str) -> tuple[int, int, int] | None:
        """Return ``(count, earliest_ingested_at_ms, latest_ingested_at_ms)``
        for ``tenant_id``, or ``None`` if no events are recorded yet."""
        row = self._conn.execute(
            """
            SELECT COUNT(*) AS n, MIN(ingested_at_ms) AS lo, MAX(ingested_at_ms) AS hi
            FROM events WHERE tenant_id = ?
            """,
            (tenant_id,),
        ).fetchone()
        if row is None or row["n"] == 0:
            return None
        return int(row["n"]), int(row["lo"]), int(row["hi"])

    def newest_event_ingested_at_ms(self, tenant_id: str) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(ingested_at_ms) AS latest FROM events WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        return int(row["latest"]) if row and row["latest"] is not None else None

    def newest_decision_ms(self, tenant_id: str, strategy: str) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(decided_at_ms) AS latest FROM attention_decisions "
            "WHERE tenant_id = ? AND strategy = ?",
            (tenant_id, strategy),
        ).fetchone()
        return int(row["latest"]) if row and row["latest"] is not None else None

    # -- Epochs -----------------------------------------------------------

    def insert_epoch(
        self,
        tenant_id: str,
        started_at_ms: int,
        grant_per_agent: int,
        seed: int,
    ) -> int:
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

    def reset_tenant(self, tenant_id: str) -> None:
        """Clear every tenant-scoped row so a scenario can restart clean.

        Used by ``POST /api/scenario/run_normal`` — the tenant row itself
        and the schema are untouched.
        """
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
            self._conn.execute(f"DELETE FROM {table} WHERE tenant_id = ?", (tenant_id,))
        self._conn.commit()

    # -- Rejected forecasts (FR-008) -----------------------------------------

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

    def list_rejected_forecasts(self, tenant_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM rejected_forecasts WHERE tenant_id = ? ORDER BY id",
            (tenant_id,),
        ).fetchall()
