"""Command entrypoint for the standalone cleaning gauntlet worker.

Production execution constructs the worker adapter around the orchestrator-owned
Gemini client and records calls through the worker's Postgres pool. ``--dry-run``
is deliberately inert: it validates local worker configuration without opening
Postgres, invoking ffmpeg, or touching runtime data.

Startup always runs stale-claim recovery before the poll loop so abandoned
``processing`` rows become claimable after a crash.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

import asyncpg

from worker.config import GauntletLimits, WorkerSettings, safe_settings_meta
from worker.fake_triage import validate_fake_mode
from worker.gemini_adapter import create_configured_triage_client
from worker.models import TriageClient
from worker.repository import GauntletRepository
from worker.service import GauntletService

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    """Parse worker command-line flags without side effects."""
    parser = argparse.ArgumentParser(description="Dialect Data Factory cleaning gauntlet")
    parser.add_argument("--dry-run", action="store_true", help="Validate configuration without side effects")
    parser.add_argument("--once", action="store_true", help="Process one job then exit")
    return parser.parse_args()


async def _run(settings: WorkerSettings, once: bool) -> None:
    """Open dependencies, recover stale claims, and run the polling service.

    Args:
        settings: Environment-derived worker settings.
        once: Whether to handle at most one job for operational diagnostics.
    """
    logger.info("_run called once=%s settings=%s", once, safe_settings_meta(settings))
    validate_fake_mode(settings)
    limits = GauntletLimits()
    pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=settings.worker_pool_min_size,
        max_size=settings.worker_pool_max_size,
    )
    repository = GauntletRepository(
        pool,
        shard_flusher_lock_id=limits.shard_flusher_lock_id,
        fingerprint_max_shift_frames=limits.fingerprint_max_shift_frames,
        fingerprint_near_distance_ratio=limits.fingerprint_near_distance_ratio,
    )
    process_id = os.getpid()
    metadata = {
        "app_environment": settings.app_environment,
        "instance_marker": settings.instance_marker,
        "fake_gemini": settings.worker_fake_gemini,
        "mode": "once" if once else "continuous",
    }
    triage_client: TriageClient | None = None
    try:
        await repository.upsert_worker_heartbeat(
            worker_id=settings.worker_id,
            process_id=process_id,
            status="starting",
            metadata=metadata,
        )
        triage_client = create_configured_triage_client(settings, pool)
        await repository.upsert_worker_heartbeat(
            worker_id=settings.worker_id,
            process_id=process_id,
            status="running",
            metadata=metadata,
        )
        service = GauntletService(
            repository,
            triage_client,
            settings.data_dir,
            limits,
            worker_id=settings.worker_id,
            process_id=process_id,
            heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
            heartbeat_metadata=metadata,
        )
        recovered = await service.recover_stale_claims()
        logger.info("startup stale claim recovery recovered=%s", recovered)
        if once:
            await service.process_once()
        else:
            await service.run_forever()
    finally:
        await _best_effort_stopping_heartbeat(
            repository=repository,
            worker_id=settings.worker_id,
            process_id=process_id,
            metadata=metadata,
        )
        if triage_client is not None:
            await triage_client.aclose()
        await pool.close()


async def _best_effort_stopping_heartbeat(
    *,
    repository: GauntletRepository,
    worker_id: str,
    process_id: int,
    metadata: dict[str, object],
) -> None:
    """Publish graceful stopping status without masking the original exit.

    Args:
        repository: Worker database boundary.
        worker_id: Stable configured worker identity.
        process_id: Current process ID.
        metadata: Redacted operational metadata.
    """
    logger.info(
        "_best_effort_stopping_heartbeat called worker_id=%s process_id=%s",
        worker_id,
        process_id,
    )
    try:
        await repository.upsert_worker_heartbeat(
            worker_id=worker_id,
            process_id=process_id,
            status="stopping",
            metadata=metadata,
        )
    except Exception as error:
        logger.warning(
            "stopping heartbeat failed worker_id=%s error_type=%s",
            worker_id,
            type(error).__name__,
        )


def main() -> None:
    """Run the worker, refusing all mutations in dry-run mode."""
    args = _parse_args()
    settings = WorkerSettings()
    logger.info("main called dry_run=%s settings=%s", args.dry_run or settings.dry_run, safe_settings_meta(settings))
    validate_fake_mode(settings)
    if args.dry_run or settings.dry_run:
        logger.info("dry-run successful: no database, ffmpeg, Gemini, or runtime-data mutation performed")
        return
    asyncio.run(_run(settings, args.once))


if __name__ == "__main__":
    main()
