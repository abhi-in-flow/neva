"""Phase 0 runtime directory and lifespan shutdown smoke tests.

Confirms startup creates the directories required by ``contracts/dirs.md`` under
an isolated temp root, and that application shutdown closes the connection
pool. No real Postgres connection is opened.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock

from httpx import AsyncClient

from app.config import Settings
from app.main import RUNTIME_SUBDIRS, configure_app_logging, ensure_runtime_directories

logger = logging.getLogger(__name__)


def test_configure_app_logging_enables_app_loggers() -> None:
    """Restore INFO emission for application loggers after disable.

    Returns:
        None.

    Side effects:
        Temporarily disables ``app.main`` then re-enables it via
        ``configure_app_logging``.
    """
    logger.info("test_configure_app_logging_enables_app_loggers called")
    app_logger = logging.getLogger("app.main")
    app_logger.disabled = True
    configure_app_logging()
    assert app_logger.disabled is False
    assert app_logger.level == logging.INFO
    assert logging.getLogger("app").handlers
    logger.info("test_configure_app_logging_enables_app_loggers completed")


def test_ensure_runtime_directories_creates_contract_layout(tmp_path: Path) -> None:
    """Create audio, decks, and corpus under an isolated data root.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        None.

    Side effects:
        Creates directories only under ``tmp_path``; does not touch ``./data``.
    """
    logger.info("test_ensure_runtime_directories_creates_contract_layout called")
    settings = Settings(data_dir=tmp_path / "runtime-data")
    created = ensure_runtime_directories(settings)
    assert settings.data_dir.is_dir()
    for name in RUNTIME_SUBDIRS:
        assert (settings.data_dir / name).is_dir()
    assert len(created) == 1 + len(RUNTIME_SUBDIRS)
    logger.info(
        "test_ensure_runtime_directories_creates_contract_layout completed "
        "created_count=%s",
        len(created),
    )


async def test_lifespan_creates_dirs_and_closes_pool(
    asgi_client: tuple[AsyncClient, AsyncMock, Path],
) -> None:
    """Exercise lifespan startup directories and shutdown pool close.

    Args:
        asgi_client: Fixture that enters and later exits FastAPI lifespan.

    Returns:
        None.

    Side effects:
        Relies on fixture teardown to close the mocked pool. Asserts runtime
        subdirectories exist under the isolated data dir after startup.
    """
    client, mock_pool, data_dir = asgi_client
    logger.info(
        "test_lifespan_creates_dirs_and_closes_pool called data_dir=%s",
        data_dir,
    )
    # Touch the app so lifespan has definitely completed startup.
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert data_dir.is_dir()
    for name in RUNTIME_SUBDIRS:
        assert (data_dir / name).is_dir()
    # Close is invoked when the AsyncClient context exits in the fixture.
    assert mock_pool.close.await_count == 0
    logger.info("test_lifespan_creates_dirs_and_closes_pool mid-check ok")


async def test_lifespan_closes_pool_on_shutdown(
    isolated_data_dir: Path,
    mock_pool: AsyncMock,
) -> None:
    """Assert the pool close hook runs when the ASGI lifespan exits.

    Args:
        isolated_data_dir: Temporary data root from the shared fixture.
        mock_pool: Pool mock returned by patched ``create_pool``.

    Returns:
        None.

    Side effects:
        Starts and stops the app lifespan once; does not contact Postgres.
    """
    from unittest.mock import patch

    from httpx import ASGITransport, AsyncClient

    from app.main import app, lifespan

    logger.info(
        "test_lifespan_closes_pool_on_shutdown called data_dir=%s",
        isolated_data_dir,
    )
    with patch("app.main.create_pool", new=AsyncMock(return_value=mock_pool)):
        async with lifespan(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/health")
                assert response.status_code == 200
                assert mock_pool.close.await_count == 0
        mock_pool.close.assert_awaited_once()
    logger.info("test_lifespan_closes_pool_on_shutdown completed")
