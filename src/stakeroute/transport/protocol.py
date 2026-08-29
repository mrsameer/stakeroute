"""The ``SignalTransport`` interface (contracts/events.md).

Both the in-process test driver (``memory.py``) and the NATS JetStream
driver (``jetstream.py``) implement this Protocol, so integration tests
never need a live broker to exercise the worker's consume loop.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SignalTransport(Protocol):
    """Publish/subscribe/ack over a named subject."""

    async def publish(self, subject: str, payload: dict) -> None:
        """Publish ``payload`` to ``subject``."""
        ...

    async def subscribe(self, subject: str, durable_name: str):
        """Return an async iterator of ``(payload, ack)`` pairs for ``subject``.

        ``ack`` is a zero-argument async callable. Delivery is at-least-once:
        a message may be redelivered if ``ack`` is never called (e.g. the
        consumer dies mid-processing).
        """
        ...
