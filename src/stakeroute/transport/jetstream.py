"""Real NATS JetStream driver against ``SignalTransport`` (D-003).

Connects to a running NATS server, ensures the ``STAKEROUTE`` stream
exists, and exposes durable pull consumers with the ordering guarantee
from contracts/events.md — file-backed, at-least-once delivery, explicit
acknowledgement, 30s ack wait, so redelivery after worker death is the
broker's own behaviour, not ours.

This is the only module in the codebase that talks to a live broker.
Everything else depends on the ``SignalTransport`` protocol; the worker's
consume loop and every subject handler are unchanged whether they run
against this driver or ``transport/memory.py`` (proven by
tests/integration/test_idempotency.py and test_crash_recovery.py, which
exercise the identical handlers over the in-process driver).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import nats
from nats.js.api import ConsumerConfig, DeliverPolicy, RetentionPolicy, StreamConfig

STREAM_NAME = "STAKEROUTE"
SUBJECTS = (
    "signals.raw",
    "forecasts.created",
    "hypotheses.updated",
    "outcomes.resolved",
)
ACK_WAIT_SECONDS = 30
FETCH_BATCH_SIZE = 100
FETCH_TIMEOUT_SECONDS = 0.5


class JetStreamTransport:
    """A ``SignalTransport`` backed by a live NATS JetStream server."""

    def __init__(self, servers: str = "nats://localhost:4222") -> None:
        self._servers = servers
        self._nc: Any = None
        self._js: Any = None
        # A worker calls subscribe() in a poll loop — once per second, per
        # subject, indefinitely. Calling pull_subscribe() fresh on every
        # call was the actual cause of a real hang reproduced while
        # verifying this against a live broker: each call binds a new
        # client-side inbox subscription without releasing the previous
        # one, and after enough loop iterations (minutes of uptime) the
        # accumulated subscriptions degraded the client until fetch()
        # never returned. One subscription object per (subject,
        # durable_name), reused for the process's lifetime, is what a pull
        # consumer is meant for.
        self._subscriptions: dict[tuple[str, str], Any] = {}

    async def connect(self) -> None:
        self._nc = await nats.connect(servers=self._servers)
        self._js = self._nc.jetstream()
        await self._ensure_stream()

    async def _ensure_stream(self) -> None:
        try:
            await self._js.add_stream(
                StreamConfig(
                    name=STREAM_NAME,
                    subjects=list(SUBJECTS),
                    retention=RetentionPolicy.LIMITS,
                )
            )
        except Exception:
            # Stream already exists from a prior run — setup is idempotent,
            # not a fresh requirement every time the process starts.
            pass

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.close()

    async def publish(self, subject: str, payload: dict) -> None:
        if self._js is None:
            raise RuntimeError("JetStreamTransport.connect() must be called first")
        await self._js.publish(subject, json.dumps(payload).encode("utf-8"))

    async def subscribe(
        self, subject: str, durable_name: str
    ) -> AsyncIterator[tuple[dict, Callable[[], Coroutine[Any, Any, None]]]]:
        """Yield ``(payload, ack)`` for one bounded batch of pending
        messages on ``subject``, via a durable pull consumer.

        Deliberately fetches AT MOST ONE batch and returns — never loops
        internally waiting for more. A worker polls several subjects in a
        round-robin (``worker/run_worker.py``): a subject fed by a
        continuous trickle (e.g. ``signals.raw`` between demo bursts) would
        otherwise keep re-satisfying an internal "keep fetching until
        nothing's left" loop forever, and the other subjects would never
        get a turn. This was a real, reproduced starvation bug — a
        `while True` here consumed `signals.raw`'s trickle indefinitely
        while `forecasts.created` and `outcomes.resolved` sat untouched —
        not a hypothetical one. The durable consumer's position is tracked
        by the broker, not by this driver, so bounding one call to one
        batch loses nothing across repeated calls.
        """
        if self._js is None:
            raise RuntimeError("JetStreamTransport.connect() must be called first")

        key = (subject, durable_name)
        subscription = self._subscriptions.get(key)
        if subscription is None:
            subscription = await self._js.pull_subscribe(
                subject,
                durable=durable_name,
                config=ConsumerConfig(
                    ack_wait=ACK_WAIT_SECONDS, deliver_policy=DeliverPolicy.ALL
                ),
            )
            self._subscriptions[key] = subscription

        try:
            messages = await subscription.fetch(
                FETCH_BATCH_SIZE, timeout=FETCH_TIMEOUT_SECONDS
            )
        except TimeoutError:
            return

        for message in messages:
            payload = json.loads(message.data.decode("utf-8"))

            async def ack(message: Any = message) -> None:
                await message.ack()

            yield payload, ack
