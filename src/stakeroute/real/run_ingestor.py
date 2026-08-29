"""Real-mode ingestor process entrypoint (D-024).

Takes the process slot ``simulator/run_simulator.py`` occupies in
docker-compose: three application processes total, unchanged. Runs
collectors and the proposal loop as coroutines sharing one observation
buffer. Phase 4 adds reasoning agents as further coroutines inside this
same process, sharing the same observation stream.

**A stated deviation from D-024's writer-count note (Principle V)**:
research.md additionally suggested the ingestor publish over the transport
rather than hold its own write connection, to keep SQLite's writer count
at the two the compose deployment already has at steady state (worker +
dashboard). Routing collectors and the proposal pipeline through new
worker-side subjects and handlers would require expanding
``worker/main.py`` with real-mode-specific logic — redaction, liveness,
duplicate detection, attribute estimation — none of which is scoped to any
task in Phase 3. This module instead holds its own ``Repository``
connection directly, the same pattern ``worker/run_worker.py`` and
``dashboard/main.py`` already use. The existing retry/WAL machinery
(``storage/repository.py``'s ``_retrying`` decorator) already anticipates
three processes opening fresh connections and writing within milliseconds
of each other at startup; this adds a third *sustained* writer rather than
a third only-at-startup one — the same class of contention D-004 already
documents as SQLite's known weak point, not a new one. Process count stays
at three, which is the constraint Complexity Tracking would otherwise
require justifying.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import time

from stakeroute.config import (
    COLLECTOR_POLL_INTERVAL_S,
    DB_PATH,
    EPOCH_GRANT,
    EVIDENCE_SCOPES,
    GEMINI_MODEL_NAME,
    GOOGLE_CLOUD_LOCATION,
    GOOGLE_CLOUD_PROJECT,
    MODEL_CEILING_CALLS_PER_HOUR,
    MODEL_TIMEOUT_S,
    OBSERVATIONS_PER_INTERVAL_LIMIT,
    PROPOSAL_INTERVAL_S,
    REAL_HYPOTHESIS_DEADLINE_MS,
    REAL_TENANT_ID,
    REPO_POLL_INTERVAL_S,
    SOURCE_SILENCE_THRESHOLD_MS,
    STAKEROUTE_MODEL,
)
from stakeroute.core.types import ObservationSnapshot
from stakeroute.model.budget import ModelBudget
from stakeroute.model.gemini import GeminiClient
from stakeroute.model.null import NullModelClient
from stakeroute.model.recorder import ModelInteractionRecorder
from stakeroute.real.collectors import (
    Collector,
    apply_volume_policy,
    ingest_raw_observation,
    update_source_liveness,
)
from stakeroute.real.collectors.app_logs import AppLogsCollector
from stakeroute.real.collectors.container_events import ContainerEventsCollector
from stakeroute.real.collectors.host_metrics import HostMetricsCollector
from stakeroute.real.collectors.vcs_tests import VcsTestsCollector
from stakeroute.real.proposal import run_proposal_cycle
from stakeroute.real.reasoners import run_agent_forecast
from stakeroute.real.scopes import EvidenceAccessScope, build_evidence_bundle
from stakeroute.storage.repository import Repository


def _build_model_client():
    if STAKEROUTE_MODEL == "gemini":
        budget = ModelBudget(MODEL_CEILING_CALLS_PER_HOUR)
        return GeminiClient(
            model_name=GEMINI_MODEL_NAME,
            project=GOOGLE_CLOUD_PROJECT,
            location=GOOGLE_CLOUD_LOCATION,
            budget=budget,
        )
    return NullModelClient()


def _build_collectors() -> list[tuple[Collector, float]]:
    """Each collector paired with its own poll interval — 2s for host
    signals, 30s for the slower-moving repository source (D-015)."""
    return [
        (HostMetricsCollector(SOURCE_SILENCE_THRESHOLD_MS), COLLECTOR_POLL_INTERVAL_S),
        (
            AppLogsCollector(
                os.environ.get("STAKEROUTE_APP_LOG_PATH", "data/stakeroute.log"),
                SOURCE_SILENCE_THRESHOLD_MS,
            ),
            COLLECTOR_POLL_INTERVAL_S,
        ),
        (
            VcsTestsCollector(
                os.environ.get("STAKEROUTE_REPO_PATH", "."),
                SOURCE_SILENCE_THRESHOLD_MS,
                test_results_path=os.environ.get("STAKEROUTE_TEST_RESULTS_PATH"),
            ),
            REPO_POLL_INTERVAL_S,
        ),
        (
            ContainerEventsCollector(SOURCE_SILENCE_THRESHOLD_MS),
            COLLECTOR_POLL_INTERVAL_S,
        ),
    ]


async def _collector_loop(
    repo: Repository,
    tenant_id: str,
    collector: Collector,
    home_dir: str,
    username: str,
    observation_buffer: list[ObservationSnapshot],
    poll_interval_s: float,
) -> None:
    while True:
        result = await collector.poll()
        now_ms = int(time.time() * 1000)
        update_source_liveness(
            repo,
            tenant_id,
            collector.source_id,
            collector.display_name,
            collector.silence_threshold_ms,
            now_ms,
            result,
        )
        for raw in result.observations:
            is_new, snapshot = ingest_raw_observation(
                repo,
                tenant_id,
                collector.source_id,
                raw,
                home_dir,
                username,
                mode="real",
                ingested_at_ms=now_ms,
            )
            if is_new:
                observation_buffer.append(snapshot)
        repo.commit()
        await asyncio.sleep(poll_interval_s)


async def _proposal_loop(
    repo: Repository,
    tenant_id: str,
    model: ModelInteractionRecorder,
    observation_buffer: list[ObservationSnapshot],
    interval_s: float,
    limit: int,
) -> None:
    while True:
        await asyncio.sleep(interval_s)
        if not observation_buffer:
            continue

        window_end_ms = int(time.time() * 1000)
        window_start_ms = window_end_ms - int(interval_s * 1000)
        pending = tuple(observation_buffer)
        observation_buffer.clear()

        retained, set_aside_count = apply_volume_policy(pending, limit)
        if set_aside_count:
            print(
                f"ingestor: volume policy set aside {set_aside_count} "
                "observation(s) this interval (still recorded in events)",
                flush=True,
            )

        result = await run_proposal_cycle(
            repo,
            tenant_id,
            model,
            retained,
            window_start_ms,
            window_end_ms,
            window_end_ms,
            MODEL_TIMEOUT_S,
            deadline_ms=window_end_ms + REAL_HYPOTHESIS_DEADLINE_MS,
        )

        if result.status == "promoted":
            print(f"ingestor: promoted hypothesis {result.hypothesis_id}", flush=True)
        elif result.status == "merged":
            print(f"ingestor: proposal merged into {result.duplicate_of}", flush=True)
        elif result.status == "rejected":
            print(
                f"ingestor: proposal rejected — {result.rejection_reason}", flush=True
            )


def _ensure_agents(repo: Repository, tenant_id: str, now_ms: int) -> None:
    """One agent per declared evidence scope (D-013) — seeded once, at
    startup, exactly like ``worker/run_worker.py`` seeds the simulated
    population."""
    for agent_id in EVIDENCE_SCOPES:
        existing = repo.get_agent(agent_id)
        if existing is not None:
            continue
        repo.upsert_agent(
            agent_id=agent_id,
            tenant_id=tenant_id,
            display_name=agent_id,
            reputation=0.5,
            available_credits=EPOCH_GRANT,
            staked_credits=0,
            attested=True,
            created_at_ms=now_ms,
        )
    repo.commit()


async def _agent_forecast_loop(
    repo: Repository,
    tenant_id: str,
    model: ModelInteractionRecorder,
    interval_s: float,
    timeout_s: float,
) -> None:
    """One coroutine per ingestor, cycling every declared agent over every
    open real-mode hypothesis, sharing the collectors' observation stream
    (D-024) — each agent still only ever sees its own declared scope
    (D-013), regardless of what else is flowing through this process.
    """
    while True:
        await asyncio.sleep(interval_s)
        now_ms = int(time.time() * 1000)
        for hypothesis in repo.list_hypotheses(tenant_id, status="open"):
            if hypothesis["mode"] != "real":
                continue
            for agent_id, source_ids in EVIDENCE_SCOPES.items():
                if repo.get_live_forecast(hypothesis["id"], agent_id) is not None:
                    continue  # one forecast per agent per hypothesis (MVP)
                scope = EvidenceAccessScope(
                    agent_id=agent_id, source_ids=source_ids, label=agent_id
                )
                bundle = build_evidence_bundle(
                    repo,
                    tenant_id,
                    scope,
                    hypothesis["id"],
                    hypothesis["statement"],
                    0,
                    now_ms,
                    now_ms,
                )
                if not bundle.observations:
                    continue  # nothing in this agent's scope to reason over yet
                result = await run_agent_forecast(
                    repo,
                    tenant_id,
                    bundle,
                    model,
                    now_ms,
                    timeout_s,
                    hypothesis["deadline_ms"],
                )
                if result is not None:
                    print(
                        f"ingestor: {agent_id} forecast "
                        f"{result.probability:.2f} on {hypothesis['id']}",
                        flush=True,
                    )


async def run() -> None:
    tenant_id = REAL_TENANT_ID
    now_ms = int(time.time() * 1000)
    repo = Repository(DB_PATH)
    repo.ensure_tenant(tenant_id, "Host Operations", now_ms)
    repo.commit()
    _ensure_agents(repo, tenant_id, now_ms)

    home_dir = os.path.expanduser("~")
    username = getpass.getuser()

    model_client = _build_model_client()
    model = ModelInteractionRecorder(
        repo,
        model_client,
        tenant_id,
        "real",
        GEMINI_MODEL_NAME if STAKEROUTE_MODEL == "gemini" else "none",
    )

    observation_buffer: list[ObservationSnapshot] = []
    collectors = _build_collectors()

    print(
        f"ingestor: starting in real mode (tenant={tenant_id}, "
        f"model={STAKEROUTE_MODEL})",
        flush=True,
    )

    tasks = [
        asyncio.create_task(
            _collector_loop(
                repo,
                tenant_id,
                collector,
                home_dir,
                username,
                observation_buffer,
                interval,
            )
        )
        for collector, interval in collectors
    ]
    tasks.append(
        asyncio.create_task(
            _proposal_loop(
                repo,
                tenant_id,
                model,
                observation_buffer,
                PROPOSAL_INTERVAL_S,
                OBSERVATIONS_PER_INTERVAL_LIMIT,
            )
        )
    )
    tasks.append(
        asyncio.create_task(
            _agent_forecast_loop(
                repo, tenant_id, model, PROPOSAL_INTERVAL_S, MODEL_TIMEOUT_S
            )
        )
    )

    try:
        await asyncio.gather(*tasks)
    finally:
        repo.close()


if __name__ == "__main__":
    asyncio.run(run())
