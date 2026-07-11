"""Configuration owned by the standalone cleaning gauntlet.

The worker reads its operational settings from environment variables without
loading or mutating application configuration. Tests provide isolated data
roots and dry-run settings so no runtime data or external GenAI is touched.

Resilience knobs (stale-claim recovery, flusher serialization, fingerprint
windowing) live in ``GauntletLimits`` so feature code never hard-codes them.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GauntletLimits:
    """Fixed worker limits centralized for testability and operational tuning.

    Attributes:
        sample_rate_hz: Archival and fingerprint decode sample rate.
        channels: Archival channel count (mono).
        poll_seconds: Idle sleep between claim attempts.
        retry_base_seconds: Base for exponential job retry backoff.
        max_tries: Attempts before a job is parked as failed.
        shard_record_limit: JSONL lines per shard before rotation.
        ffmpeg_timeout_seconds: Bound for transcode and PCM decode.
        stale_claim_seconds: Age after which a processing claim is abandoned.
        stale_claim_interval_seconds: How often idle loops re-run recovery.
        shard_flusher_lock_id: Postgres advisory lock serializing shard writes.
        fingerprint_frame_ms: RMS frame size for the acoustic envelope.
        fingerprint_max_shift_frames: Allowed envelope alignment shift.
        fingerprint_near_distance_ratio: Max normalized L1 distance for near-dup.
    """

    sample_rate_hz: int = 16_000
    channels: int = 1
    poll_seconds: float = 1.0
    retry_base_seconds: float = 2.0
    max_tries: int = 3
    shard_record_limit: int = 500
    ffmpeg_timeout_seconds: float = 30.0
    stale_claim_seconds: float = 120.0
    stale_claim_interval_seconds: float = 30.0
    shard_flusher_lock_id: int = 2_041_993_771
    fingerprint_frame_ms: int = 50
    fingerprint_max_shift_frames: int = 6
    fingerprint_near_distance_ratio: float = 0.12


class WorkerSettings(BaseSettings):
    """Environment-backed settings for a worker process.

    Attributes:
        database_url: Async Postgres DSN used only outside dry-run mode.
        data_dir: Root containing contract-defined audio and corpus paths.
        dry_run: Makes the entrypoint refuse DB and live GenAI work.
        worker_pool_min_size: Minimum asyncpg connections for the worker.
        worker_pool_max_size: Maximum asyncpg connections for the worker.
        worker_id: Stable heartbeat identity; configure uniquely per replica.
        heartbeat_interval_seconds: Frequency of liveness upserts.
        heartbeat_stale_seconds: Maximum heartbeat age accepted by healthcheck.
        app_environment: Deployment environment used to gate fake Gemini.
        instance_marker: Explicit isolated-instance marker for load testing.
        worker_fake_gemini: Enables the deterministic fake only when safely gated.
        worker_fake_gemini_delay_seconds: Artificial fake-client latency.
        worker_fake_gemini_failure_rate: Deterministic fake failure ratio.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://dialect:dialect_dev_only@localhost:5432/dialect_factory"
    data_dir: Path = Path("data")
    dry_run: bool = False
    worker_pool_min_size: int = Field(default=1, ge=1, le=16)
    worker_pool_max_size: int = Field(default=4, ge=1, le=32)
    worker_id: str = Field(default_factory=socket.gethostname, min_length=1, max_length=128)
    heartbeat_interval_seconds: float = Field(default=10.0, gt=0)
    heartbeat_stale_seconds: float = Field(default=45.0, gt=0)
    app_environment: str = "development"
    instance_marker: str = ""
    worker_fake_gemini: bool = False
    worker_fake_gemini_delay_seconds: float = Field(default=0.0, ge=0)
    worker_fake_gemini_failure_rate: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_operational_bounds(self) -> "WorkerSettings":
        """Reject invalid pool and heartbeat relationships.

        Returns:
            The validated settings instance.

        Raises:
            ValueError: If pool min exceeds max or stale time is not longer
                than the heartbeat interval.
        """
        logger.info(
            "validate_operational_bounds called pool_min=%s pool_max=%s heartbeat_interval=%s "
            "heartbeat_stale=%s",
            self.worker_pool_min_size,
            self.worker_pool_max_size,
            self.heartbeat_interval_seconds,
            self.heartbeat_stale_seconds,
        )
        if self.worker_pool_min_size > self.worker_pool_max_size:
            raise ValueError("worker_pool_min_size must not exceed worker_pool_max_size")
        if self.heartbeat_stale_seconds <= self.heartbeat_interval_seconds:
            raise ValueError("heartbeat_stale_seconds must exceed heartbeat_interval_seconds")
        return self


def safe_settings_meta(settings: WorkerSettings) -> dict[str, object]:
    """Return non-secret configuration metadata for INFO logs.

    Args:
        settings: Current worker settings.

    Returns:
        Log-safe paths and mode flags, excluding the database credentials.
    """
    logger.info(
        "safe_settings_meta called data_dir=%s dry_run=%s worker_id=%s",
        settings.data_dir,
        settings.dry_run,
        settings.worker_id,
    )
    return {
        "data_dir": str(settings.data_dir),
        "dry_run": settings.dry_run,
        "worker_id": settings.worker_id,
        "pool_min_size": settings.worker_pool_min_size,
        "pool_max_size": settings.worker_pool_max_size,
        "heartbeat_interval_seconds": settings.heartbeat_interval_seconds,
        "heartbeat_stale_seconds": settings.heartbeat_stale_seconds,
        "app_environment": settings.app_environment,
        "instance_marker": settings.instance_marker,
        "fake_gemini": settings.worker_fake_gemini,
        "fake_delay_seconds": settings.worker_fake_gemini_delay_seconds,
        "fake_failure_rate": settings.worker_fake_gemini_failure_rate,
    }
