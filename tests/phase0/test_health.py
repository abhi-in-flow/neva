"""Phase 0 health endpoint smoke tests with a mocked database pool.

Verifies ``GET /api/health`` returns the Wave 0 acceptance payload without
mutating Postgres. The pool is injected through FastAPI lifespan via a patch
on ``create_pool``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, call

from httpx import AsyncClient

logger = logging.getLogger(__name__)


async def test_health_returns_ok_with_mocked_pool(
    asgi_client: tuple[AsyncClient, AsyncMock, Path],
) -> None:
    """Assert health reports connected status using the mocked pool.

    Args:
        asgi_client: Fixture providing client, pool mock, and temp data dir.

    Returns:
        None.

    Side effects:
        Issues one HTTP GET against the in-process ASGI app. Confirms
        ``fetchval`` was awaited with ``SELECT 1``.
    """
    client, mock_pool, _data_dir = asgi_client
    logger.info("test_health_returns_ok_with_mocked_pool called")
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "connected",
        "environment": "development",
        "instance_marker": None,
        "database_name": "dialect_factory",
    }
    assert mock_pool.fetchval.await_args_list == [
        call("SELECT 1"),
        call("SELECT current_database()"),
    ]
    logger.info("test_health_returns_ok_with_mocked_pool completed")
