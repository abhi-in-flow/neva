"""Application configuration for the Dialect Data Factory backend.

Centralizes environment-backed settings used by the FastAPI app, GenAI client,
and operational scripts: database connectivity, runtime data paths, pool
sizing, game limits, and Gemini concurrency/retry controls. Callers must
obtain settings through ``get_settings`` so values stay cached and consistent
within a process. This module is the only place that loads ``.env`` for the
application package; it never logs secret values such as ``gemini_api_key``.

Architectural boundary:
- Owned by the Wave 1 shared GenAI / orchestrator path set.
- Worker and deckgen may read settings for Gemini knobs but must not embed
  magic rate limits or model strings in feature code.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Typed runtime settings loaded from environment variables and ``.env``.

    Attributes:
        database_url: Async Postgres DSN used by asyncpg.
        data_dir: Root directory for audio, decks, and corpus blobs.
        rounds_cap: Maximum rounds per player session.
        db_pool_min_size: Minimum asyncpg pool connections.
        db_pool_max_size: Maximum asyncpg pool connections.
        gemini_api_key: Google GenAI API key (never logged).
        gemini_max_retries: Retries after the first attempt for transient errors.
        gemini_retry_base_delay_s: Initial exponential backoff delay in seconds.
        gemini_retry_max_delay_s: Cap on exponential backoff delay in seconds.
        gemini_flash_max_concurrency: In-flight cap for Gemini Flash calls.
        gemini_flash_rpm: Soft per-minute request budget for Gemini Flash.
        nano_banana_max_concurrency: In-flight cap for Nano Banana image calls.
        nano_banana_rpm: Soft per-minute request budget for Nano Banana.
        nano_banana_cost_microusd_per_image: Best-effort cost estimate per image.
        deck_admin_api_key: Shared demo-only key protecting deck-control routes.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://dialect:dialect_dev_only@localhost:5432/dialect_factory"
    data_dir: Path = Path("data")
    rounds_cap: int = 20
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10

    gemini_api_key: str = ""
    gemini_max_retries: int = 3
    gemini_retry_base_delay_s: float = 0.5
    gemini_retry_max_delay_s: float = 8.0
    gemini_flash_max_concurrency: int = 4
    gemini_flash_rpm: int = 60
    nano_banana_max_concurrency: int = 2
    nano_banana_rpm: int = 30
    nano_banana_cost_microusd_per_image: int = 33600
    deck_admin_api_key: str = ""


def database_log_meta(database_url: str) -> dict[str, object]:
    """Return non-secret connection metadata suitable for INFO logs.

    Args:
        database_url: Full Postgres DSN that may contain credentials.

    Returns:
        A dict with host, port, database name, and scheme only. Credentials
        and query parameters are omitted.
    """
    logger.info(
        "database_log_meta called url_length=%s",
        len(database_url),
    )
    parsed = urlparse(database_url)
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "port": parsed.port,
        "database": parsed.path.lstrip("/") or None,
    }


def gemini_settings_log_meta(settings: Settings) -> dict[str, object]:
    """Return safe Gemini configuration metadata for INFO logs.

    Args:
        settings: Loaded application settings.

    Returns:
        A dict of concurrency, RPM, and retry knobs. The API key is never
        included; only whether it is configured and its length.
    """
    logger.info(
        "gemini_settings_log_meta called key_configured=%s",
        bool(settings.gemini_api_key),
    )
    return {
        "key_configured": bool(settings.gemini_api_key),
        "key_length": len(settings.gemini_api_key),
        "max_retries": settings.gemini_max_retries,
        "retry_base_delay_s": settings.gemini_retry_base_delay_s,
        "retry_max_delay_s": settings.gemini_retry_max_delay_s,
        "flash_max_concurrency": settings.gemini_flash_max_concurrency,
        "flash_rpm": settings.gemini_flash_rpm,
        "nano_banana_max_concurrency": settings.nano_banana_max_concurrency,
        "nano_banana_rpm": settings.nano_banana_rpm,
        "nano_banana_cost_microusd_per_image": settings.nano_banana_cost_microusd_per_image,
        "deck_admin_key_configured": bool(settings.deck_admin_api_key),
    }


@lru_cache
def get_settings() -> Settings:
    """Load and cache application settings for the current process.

    Returns:
        A ``Settings`` instance populated from environment variables and the
        optional ``.env`` file. Subsequent calls return the same cached object.

    Side effects:
        Reads environment / ``.env`` on the first call only. Logs safe
        configuration metadata without credentials or API keys.
    """
    settings = Settings()
    logger.info(
        "get_settings called data_dir=%s rounds_cap=%s db_pool_min_size=%s "
        "db_pool_max_size=%s database=%s gemini=%s",
        settings.data_dir,
        settings.rounds_cap,
        settings.db_pool_min_size,
        settings.db_pool_max_size,
        database_log_meta(settings.database_url),
        gemini_settings_log_meta(settings),
    )
    return settings
