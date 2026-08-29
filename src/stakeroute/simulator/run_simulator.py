"""Standalone simulator process entrypoint (Phase 7, D-008).

Publishes every signal and forecast through JetStream exactly as
contracts/events.md specifies. After the initial batch, keeps a trickle of
harmless signals flowing indefinitely — the continued traffic the
failure-recovery demonstration needs while the worker is deliberately down
(quickstart V4: "let the simulator keep publishing for ~15 seconds").

Deliberately does NOT open its own database connection. SQLite tolerates
one sustained writer far better than several (D-004's documented
limitation), and this process already shares the file with the worker and
the dashboard — three fresh connections all writing within the same
startup window was a genuine, reproduced deadlock, not a theoretical risk.
The worker owns seeding the world's hypotheses and agents at startup
(``run_worker.py``) precisely so this process never has to.
"""

from __future__ import annotations

import asyncio
import os
import time

from stakeroute.config import DEFAULT_TENANT_ID, NATS_URL
from stakeroute.core.types import compute_event_id
from stakeroute.simulator.scenarios import generate_world
from stakeroute.transport.jetstream import JetStreamTransport
from stakeroute.worker.main import FORECASTS_CREATED, SIGNALS_RAW

SEED = int(os.environ.get("SCENARIO_SEED", "42"))
TRICKLE_INTERVAL_SECONDS = 1.0


async def _publish_signal(
    transport: JetStreamTransport,
    tenant_id: str,
    source: str,
    source_event_id: str,
    observed_at_ms: int,
    payload: dict,
) -> None:
    event_id = compute_event_id(tenant_id, source, source_event_id, observed_at_ms)
    await transport.publish(
        SIGNALS_RAW,
        {
            "tenant_id": tenant_id,
            "event_id": event_id,
            "emitted_at_ms": observed_at_ms,
            "payload": {
                "source": source,
                "source_event_id": source_event_id,
                "provenance": {"system": "simulator", "collector": "sim"},
                "payload": payload,
            },
        },
    )


async def run() -> None:
    tenant_id = DEFAULT_TENANT_ID
    now = int(time.time() * 1000)
    world = generate_world(seed=SEED, reference_ms=now)

    transport = JetStreamTransport(servers=NATS_URL)
    await transport.connect()
    print(f"simulator: connected to {NATS_URL}, seed={SEED}", flush=True)

    for signal in world.signals:
        await _publish_signal(
            transport,
            tenant_id,
            signal.source,
            signal.source_event_id,
            signal.observed_at_ms,
            signal.payload,
        )

    for forecast in world.forecasts:
        event_id = compute_event_id(
            tenant_id, "forecasts", forecast.source_event_id, now
        )
        await transport.publish(
            FORECASTS_CREATED,
            {
                "tenant_id": tenant_id,
                "event_id": event_id,
                "emitted_at_ms": now,
                "payload": {
                    "hypothesis_id": forecast.hypothesis_id,
                    "agent_id": forecast.agent_id,
                    "probability": forecast.probability,
                    "stake": forecast.stake,
                    "evidence_cluster_id": forecast.evidence_cluster_id,
                    "evidence_refs": [],
                    "source_event_id": forecast.source_event_id,
                    "expires_at_ms": now + 3_600_000,
                },
            },
        )
    print(
        f"simulator: published {len(world.signals)} signals, "
        f"{len(world.forecasts)} forecasts",
        flush=True,
    )

    i = 0
    while True:
        await asyncio.sleep(TRICKLE_INTERVAL_SECONDS)
        i += 1
        ts = int(time.time() * 1000)
        await _publish_signal(
            transport,
            tenant_id,
            "metrics",
            f"trickle-{i}",
            ts,
            {"metric": "heartbeat", "value": i},
        )


if __name__ == "__main__":
    asyncio.run(run())
