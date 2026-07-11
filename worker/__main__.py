"""Command entrypoint for the standalone cleaning gauntlet worker.

Production execution constructs the worker adapter around the orchestrator-owned
Gemini client and records calls through the worker's Postgres pool. ``--dry-run``
is deliberately inert: it validates local worker configuration without opening
Postgres, invoking ffmpeg, or touching runtime data.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import asyncpg

from worker.config import GauntletLimits, WorkerSettings, safe_settings_meta
from worker.gemini_adapter import create_triage_client
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
    """Open dependencies and run the polling service.

    Args:
        settings: Environment-derived worker settings.
        once: Whether to handle at most one job for operational diagnostics.
    """
    logger.info("_run called once=%s settings=%s", once, safe_settings_meta(settings))
    pool = await asyncpg.create_pool(settings.database_url)
    triage_client = create_triage_client(pool)
    try:
        service = GauntletService(
            GauntletRepository(pool),
            triage_client,
            settings.data_dir,
            GauntletLimits(),
        )
        if once:
            await service.process_once()
        else:
            await service.run_forever()
    finally:
        await triage_client.aclose()
        await pool.close()


def main() -> None:
    """Run the worker, refusing all mutations in dry-run mode."""
    args = _parse_args()
    settings = WorkerSettings()
    logger.info("main called dry_run=%s settings=%s", args.dry_run or settings.dry_run, safe_settings_meta(settings))
    if args.dry_run or settings.dry_run:
        logger.info("dry-run successful: no database, ffmpeg, Gemini, or runtime-data mutation performed")
        return
    asyncio.run(_run(settings, args.once))


if __name__ == "__main__":
    main()
