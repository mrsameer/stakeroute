"""In-process ``SignalTransport`` driver — no broker, no Docker.

Backs the entire integration test suite. Mirrors JetStream's durable pull
consumer model closely enough to prove idempotency and crash recovery
(T068, T069) without infrastructure: messages delivered via ``subscribe``
stay "in flight" until their ``ack`` callable is awaited, and
``simulate_crash`` moves any un-acked messages back onto the queue exactly
as a broker would redeliver to a consumer that died mid-processing.
"""

from __future__ import annotations

import itertools
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any


class MemoryTransport:
    """An in-memory stand-in for JetStream, implementing ``SignalTransport``."""

    def __init__(self, max_redeliveries: int = 5) -> None:
        self._pending: dict[str, deque[tuple[int, dict, int]]] = defaultdict(deque)
        self._in_flight: dict[str, dict[int, tuple[dict, int]]] = defaultdict(dict)
        self._dead_letter: dict[str, list[dict]] = defaultdict(list)
        self._max_redeliveries = max_redeliveries
        self._next_id = itertools.count(1)

    async def publish(self, subject: str, payload: dict) -> None:
        msg_id = next(self._next_id)
        self._pending[subject].append((msg_id, payload, 0))

    async def subscribe(
        self, subject: str, durable_name: str
    ) -> AsyncIterator[tuple[dict, Callable[[], Coroutine[Any, Any, None]]]]:
        """Yield ``(payload, ack)`` for every currently pending message.

        Ends once the queue is drained rather than blocking forever — tests
        drive delivery explicitly by publishing, then subscribing, then
        optionally crashing before ack and subscribing again.
        """
        queue = self._pending[subject]
        while queue:
            msg_id, payload, delivery_count = queue.popleft()
            self._in_flight[subject][msg_id] = (payload, delivery_count)

            async def ack(subject: str = subject, msg_id: int = msg_id) -> None:
                self._in_flight[subject].pop(msg_id, None)

            yield payload, ack

    def simulate_crash(self, subject: str) -> int:
        """Redeliver every un-acked message on ``subject``.

        Returns the number of messages redelivered. A message that has
        already been redelivered ``max_redeliveries`` times is moved to the
        dead-letter list instead of being requeued again.
        """
        in_flight = self._in_flight[subject]
        redelivered = 0
        for msg_id in sorted(in_flight):
            payload, delivery_count = in_flight[msg_id]
            if delivery_count >= self._max_redeliveries:
                self._dead_letter[subject].append(payload)
                continue
            self._pending[subject].append((msg_id, payload, delivery_count + 1))
            redelivered += 1
        in_flight.clear()
        return redelivered

    def dead_letters(self, subject: str) -> list[dict]:
        return list(self._dead_letter[subject])

    def pending_count(self, subject: str) -> int:
        return len(self._pending[subject])

    def in_flight_count(self, subject: str) -> int:
        return len(self._in_flight[subject])
