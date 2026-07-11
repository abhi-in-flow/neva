"""Async Postgres pool helpers for the Dialect Data Factory API.

Owns creation of the shared ``asyncpg`` connection pool used by FastAPI
request handlers. Pool sizing comes from ``app.config.Settings``; this module
does not embed magic limits. Callers must close the returned pool during
application shutdown. Connection credentials are never logged.
"""

from __future__ import annotations

import logging

import asyncpg

from app.config import Settings, database_log_meta

logger = logging.getLogger(__name__)


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """Create an asyncpg connection pool from application settings.

    Args:
        settings: Loaded settings providing the DSN and pool size bounds.

    Returns:
        An open ``asyncpg.Pool`` ready for query execution.

    Side effects:
        Opens TCP connections to Postgres. Logs safe connection metadata and
        pool sizing; does not log credentials or the raw DSN.
    """
    logger.info(
        "create_pool called min_size=%s max_size=%s database=%s",
        settings.db_pool_min_size,
        settings.db_pool_max_size,
        database_log_meta(settings.database_url),
    )
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
    )
    logger.info("create_pool completed pool_acquired=True")
    return pool
