"""Shared fixtures for Phase 0 non-destructive smoke tests.

Provides temporary data directories, cleared settings cache, and a mocked
asyncpg pool so FastAPI lifespan and health checks can run without touching
the development database. httpx 0.28 does not enter ASGI lifespan, so fixtures
drive ``app.main.lifespan`` explicitly.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.main import app, lifespan

logger = logging.getLogger(__name__)


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point ``DATA_DIR`` at a temporary directory and clear settings cache.

    Args:
        tmp_path: Pytest temporary directory root.
        monkeypatch: Pytest monkeypatch fixture.

    Yields:
        The temporary data directory path used for the test.

    Side effects:
        Sets ``DATA_DIR`` in the process environment and clears
        ``get_settings`` cache before and after the test.
    """
    logger.info("isolated_data_dir fixture setup")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    yield data_dir
    get_settings.cache_clear()
    logger.info("isolated_data_dir fixture teardown")


@pytest.fixture
def mock_pool() -> AsyncMock:
    """Build an asyncpg-like pool mock for health and lifespan tests.

    Returns:
        An ``AsyncMock`` with ``fetchval`` returning ``1`` and an awaitable
        ``close`` method.
    """
    logger.info("mock_pool fixture setup")
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1)
    pool.close = AsyncMock()
    return pool


@pytest.fixture
async def asgi_client(
    isolated_data_dir: Path,
    mock_pool: AsyncMock,
) -> AsyncIterator[tuple[AsyncClient, AsyncMock, Path]]:
    """ASGI test client with mocked pool creation and isolated data dir.

    Args:
        isolated_data_dir: Temporary runtime data root.
        mock_pool: Shared pool mock injected via ``create_pool``.

    Yields:
        Tuple of ``(client, mock_pool, isolated_data_dir)``. Lifespan runs on
        enter and exit so startup directories and shutdown close are exercised.

    Side effects:
        Patches ``app.main.create_pool`` and enters ``lifespan`` for the client
        lifetime. Does not open a real database connection.
    """
    logger.info(
        "asgi_client fixture setup data_dir=%s",
        isolated_data_dir,
    )
    with patch("app.main.create_pool", new=AsyncMock(return_value=mock_pool)):
        async with lifespan(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield client, mock_pool, isolated_data_dir
    logger.info("asgi_client fixture teardown")
