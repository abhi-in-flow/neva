"""Compose-suitable database heartbeat healthcheck for the gauntlet worker.

Run with ``python -m worker.health`` in the same environment as the worker.
The command opens a bounded asyncpg pool, checks the configured ``WORKER_ID``,
and exits nonzero when the row is missing, not ``running``, or older than
``HEARTBEAT_STALE_SECONDS``. It has no FastAPI dependency.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from worker.config import GauntletLimits, WorkerSettings
from worker.repository import GauntletRepository, WorkerHeartbeatHealth

logger = logging.getLogger(__name__)


async def read_worker_health(settings: WorkerSettings) -> WorkerHeartbeatHealth:
    """Read heartbeat health through a bounded transient pool.

    Args:
        settings: Database, pool, worker ID, and stale-threshold configuration.

    Returns:
        Structured worker heartbeat health.
    """
    logger.info(
        "read_worker_health called worker_id=%s stale_after_seconds=%s pool_min=%s pool_max=%s",
        settings.worker_id,
        settings.heartbeat_stale_seconds,
        settings.worker_pool_min_size,
        settings.worker_pool_max_size,
    )
    limits = GauntletLimits()
    pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=settings.worker_pool_min_size,
        max_size=settings.worker_pool_max_size,
    )
    try:
        repository = GauntletRepository(
            pool,
            shard_flusher_lock_id=limits.shard_flusher_lock_id,
            fingerprint_max_shift_frames=limits.fingerprint_max_shift_frames,
            fingerprint_near_distance_ratio=limits.fingerprint_near_distance_ratio,
        )
        return await repository.get_worker_heartbeat_health(
            worker_id=settings.worker_id,
            stale_after_seconds=settings.heartbeat_stale_seconds,
        )
    finally:
        await pool.close()


async def health_exit_code(settings: WorkerSettings) -> int:
    """Return a process exit code for the configured heartbeat.

    Args:
        settings: Worker settings used by the healthcheck.

    Returns:
        Zero for a recent running heartbeat, otherwise one.
    """
    logger.info("health_exit_code called worker_id=%s", settings.worker_id)
    health = await read_worker_health(settings)
    logger.info(
        "worker health worker_id=%s exists=%s status=%s heartbeat_at=%s healthy=%s",
        health.worker_id,
        health.exists,
        health.status,
        health.heartbeat_at,
        health.healthy,
    )
    return 0 if health.healthy else 1


def main() -> None:
    """Run the configured heartbeat check and exit for Compose health status."""
    settings = WorkerSettings()
    logger.info("worker health main called worker_id=%s", settings.worker_id)
    raise SystemExit(asyncio.run(health_exit_code(settings)))


if __name__ == "__main__":
    main()
