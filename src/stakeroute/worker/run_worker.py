"""Standalone worker process entrypoint (Phase 7, D-008).

Runs the exact consume loop and subject handlers exercised by
tests/integration/test_idempotency.py and test_crash_recovery.py against
the in-process driver — here, against a real JetStream connection. This
is the process ``docker compose kill worker`` targets in the
failure-recovery demonstration (SC-005, quickstart V4).

This process is the sole writer to the shared SQLite file: it seeds the
scenario's hypotheses and agents at startup (there is no event subject for
either — they are simulator-known ground truth, not pipeline-derived
state) as well as applying every subject's effects. The simulator publishes
events only and never opens its own database connection — three processes
each writing within the same startup window is exactly the sustained
multi-writer contention D-004 already flags as SQLite's weak point, and
splitting reads (dashboard) from the one writer (this process) is the
cheapest way to stay clear of it.
"""

from __future__ import annotations

import asyncio
import os
import time

from stakeroute.config import (
    ATTENTION_BUDGET,
    DB_PATH,
    DEFAULT_TENANT_ID,
    EPOCH_GRANT,
    NATS_URL,
)
from stakeroute.simulator.scenarios import generate_world
from stakeroute.storage.repository import Repository
from stakeroute.transport.jetstream import JetStreamTransport
from stakeroute.worker.main import (
    FORECASTS_CREATED,
    OUTCOMES_RESOLVED,
    SIGNALS_RAW,
    consume_once,
    handle_forecast_created,
    handle_outcome_resolved,
    handle_signal,
)
from stakeroute.worker.pipeline import publish_hypothesis_updates, run_ranking_pass

POLL_INTERVAL_SECONDS = 0.2
SEED = int(os.environ.get("SCENARIO_SEED", "42"))


def _seed_world(repo: Repository, tenant_id: str, now_ms: int) -> None:
    """Seed the scenario's hypotheses and agents — but only on a genuine
    first start. A worker restart (the whole point of the kill-and-recover
    demo, SC-005) must resume against existing state, not wipe it: calling
    ``reset_tenant`` unconditionally here would erase every signal and
    forecast the pipeline had already processed before the kill, silently
    defeating the durability claim on every restart.
    """
    if repo.list_hypotheses(tenant_id):
        print(
            f"worker: existing state found (tenant={tenant_id}); resuming", flush=True
        )
        return

    world = generate_world(seed=SEED, reference_ms=now_ms)
    for hypothesis in world.hypotheses:
        repo.upsert_hypothesis(
            hypothesis_id=hypothesis.id,
            tenant_id=tenant_id,
            statement=hypothesis.statement,
            prior_probability=hypothesis.prior_probability,
            impact_minor_units=hypothesis.impact_minor_units,
            urgency=hypothesis.urgency,
            review_cost=hypothesis.review_cost,
            deadline_ms=hypothesis.deadline_ms,
            status="open",
            created_at_ms=hypothesis.created_at_ms,
        )
    for agent in world.agents:
        repo.upsert_agent(
            agent_id=agent.agent_id,
            tenant_id=tenant_id,
            display_name=agent.display_name,
            reputation=agent.starting_reputation,
            available_credits=EPOCH_GRANT,
            staked_credits=0,
            attested=agent.attested,
            created_at_ms=now_ms,
        )
    repo.commit()
    print(
        f"worker: seeded {len(world.hypotheses)} hypotheses, "
        f"{len(world.agents)} agents (tenant={tenant_id}, seed={SEED})",
        flush=True,
    )


async def run() -> None:
    tenant_id = DEFAULT_TENANT_ID
    now_ms = int(time.time() * 1000)
    repo = Repository(DB_PATH)
    repo.ensure_tenant(tenant_id, "AcmePay", now_ms)
    repo.commit()
    _seed_world(repo, tenant_id, now_ms)

    transport = JetStreamTransport(servers=NATS_URL)
    await transport.connect()
    print(f"worker: connected to {NATS_URL}, tenant={tenant_id}", flush=True)

    try:
        while True:
            await consume_once(
                transport, SIGNALS_RAW, "worker-signals", repo, tenant_id, handle_signal
            )
            applied = await consume_once(
                transport,
                FORECASTS_CREATED,
                "worker-forecasts",
                repo,
                tenant_id,
                handle_forecast_created,
            )
            resolved = await consume_once(
                transport,
                OUTCOMES_RESOLVED,
                "worker-outcomes",
                repo,
                tenant_id,
                handle_outcome_resolved,
            )
            if applied or resolved:
                now_ms = int(time.time() * 1000)
                results = run_ranking_pass(repo, tenant_id, ATTENTION_BUDGET, now_ms)
                await publish_hypothesis_updates(transport, tenant_id, results, now_ms)
                print(
                    f"worker: applied {applied} forecast(s), {resolved} outcome(s); "
                    "ranking pass republished",
                    flush=True,
                )
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    finally:
        await transport.close()
        repo.close()


if __name__ == "__main__":
    asyncio.run(run())
