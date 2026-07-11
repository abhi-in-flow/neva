"""Apply ordered forward migrations to an existing development database.

The canonical schema creates a fresh database at the latest contract revision,
while files under ``contracts/migrations`` upgrade databases created by earlier
revisions. This script records applied filenames in ``schema_migrations`` and
executes each pending file transactionally. ``--dry-run`` lists pending files
without connecting mutating SQL to the database.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import asyncpg

from app.config import database_log_meta, get_settings

logger = logging.getLogger(__name__)

MIGRATIONS_RELATIVE_PATH = Path("contracts") / "migrations"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse migration-runner arguments.

    Args:
        argv: Optional argument vector used by tests; defaults to process args.

    Returns:
        Parsed arguments containing the dry-run flag.
    """
    logger.info("parse_args called argv_length=%s", 0 if argv is None else len(argv))
    parser = argparse.ArgumentParser(description="Apply pending contract migrations.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List migration files without changing the database.",
    )
    return parser.parse_args(argv)


def discover_migrations() -> list[Path]:
    """Return migration SQL files in deterministic filename order.

    Returns:
        Absolute paths for all ``*.sql`` files under contracts/migrations.
    """
    directory = Path(__file__).resolve().parents[1] / MIGRATIONS_RELATIVE_PATH
    migrations = sorted(directory.glob("*.sql"))
    logger.info(
        "discover_migrations called directory=%s migration_count=%s",
        directory,
        len(migrations),
    )
    return migrations


async def apply_migrations(*, dry_run: bool = False) -> list[str]:
    """Apply every migration not already recorded by filename.

    Args:
        dry_run: When true, return discovered filenames without database writes.

    Returns:
        Migration filenames that were pending in dry-run mode or applied in
        normal mode.

    Side effects:
        Normal mode creates ``schema_migrations`` and transactionally executes
        pending SQL files. Dry-run mode performs no database connection or write.
    """
    settings = get_settings()
    migrations = discover_migrations()
    logger.info(
        "apply_migrations called dry_run=%s migration_count=%s database=%s",
        dry_run,
        len(migrations),
        database_log_meta(settings.database_url),
    )
    if dry_run:
        names = [path.name for path in migrations]
        logger.info("apply_migrations dry-run complete migration_names=%s", names)
        return names

    connection = await asyncpg.connect(settings.database_url)
    applied: list[str] = []
    try:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        existing = set(await connection.fetchval("SELECT array_agg(filename) FROM schema_migrations") or [])
        for path in migrations:
            if path.name in existing:
                logger.info("migration skipped filename=%s reason=already_applied", path.name)
                continue
            sql_text = path.read_text(encoding="utf-8")
            logger.info(
                "migration applying filename=%s sql_bytes=%s",
                path.name,
                len(sql_text.encode("utf-8")),
            )
            async with connection.transaction():
                await connection.execute(sql_text)
                await connection.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)",
                    path.name,
                )
            applied.append(path.name)
    finally:
        await connection.close()
    logger.info("apply_migrations completed applied=%s", applied)
    return applied


async def main(argv: list[str] | None = None) -> int:
    """Run the migration command-line workflow.

    Args:
        argv: Optional arguments for testing; defaults to process arguments.

    Returns:
        Zero when discovery or application succeeds.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = parse_args(argv)
    logger.info("main called dry_run=%s", args.dry_run)
    await apply_migrations(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
