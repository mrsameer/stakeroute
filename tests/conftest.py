"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from stakeroute.config import REAL_TENANT_ID
from stakeroute.storage.repository import Repository


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests against asyncio only (no trio dependency)."""
    return "asyncio"


@pytest.fixture
def real_repo(tmp_path) -> Repository:
    """A ``Repository`` on a scratch database, seeded with the real tenant.

    Every real-mode test starts here rather than against ``acmepay`` — the
    tenant boundary (D-018) is exercised even in the fast unit/integration
    loop, not just in end-to-end scenarios.
    """
    repo = Repository(str(tmp_path / "real_mode.db"))
    repo.ensure_tenant(REAL_TENANT_ID, "Host Operations", 0)
    repo.commit()
    return repo


@pytest.fixture
def null_model():
    """A ``NullModelClient`` — always rejects, always ``state() ==
    'unconfigured'`` (FR-126). Imported lazily so this fixture module can be
    collected before ``stakeroute.model`` exists (Phase 1 checkpoint)."""
    from stakeroute.model.null import NullModelClient

    return NullModelClient()


class FrozenClock:
    """A controllable clock for tests that need explicit ``*_ms`` values.

    The core and every pure module still take timestamps as arguments
    (Principle I) — this fixture exists so real-mode integration tests can
    advance time deliberately (silence thresholds, epoch rollovers) without
    sleeping.
    """

    def __init__(self, start_ms: int = 1_700_000_000_000) -> None:
        self._now_ms = start_ms

    def now_ms(self) -> int:
        return self._now_ms

    def advance(self, delta_ms: int) -> int:
        self._now_ms += delta_ms
        return self._now_ms


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock()
