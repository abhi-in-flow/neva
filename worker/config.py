"""Configuration owned by the standalone cleaning gauntlet.

The worker reads its operational settings from environment variables without
loading or mutating application configuration. Tests provide isolated data
roots and dry-run settings so no runtime data or external GenAI is touched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GauntletLimits:
    """Fixed worker limits centralized for testability and operational tuning."""

    sample_rate_hz: int = 16_000
    channels: int = 1
    poll_seconds: float = 1.0
    retry_base_seconds: float = 2.0
    max_tries: int = 3
    shard_record_limit: int = 500
    ffmpeg_timeout_seconds: float = 30.0


class WorkerSettings(BaseSettings):
    """Environment-backed settings for a worker process.

    Attributes:
        database_url: Async Postgres DSN used only outside dry-run mode.
        data_dir: Root containing contract-defined audio and corpus paths.
        dry_run: Makes the entrypoint refuse DB and live GenAI work.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://dialect:dialect_dev_only@localhost:5432/dialect_factory"
    data_dir: Path = Path("data")
    dry_run: bool = False


def safe_settings_meta(settings: WorkerSettings) -> dict[str, object]:
    """Return non-secret configuration metadata for INFO logs.

    Args:
        settings: Current worker settings.

    Returns:
        Log-safe paths and mode flags, excluding the database credentials.
    """
    logger.info("safe_settings_meta called data_dir=%s dry_run=%s", settings.data_dir, settings.dry_run)
    return {"data_dir": str(settings.data_dir), "dry_run": settings.dry_run}
