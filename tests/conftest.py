"""Shared pytest fixtures."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests against asyncio only (no trio dependency)."""
    return "asyncio"
