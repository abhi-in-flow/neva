"""Idempotently bootstrap or migrate the configured Postgres database.

Fresh databases receive the latest ``contracts/schema.sql`` atomically and are
marked at the current migration revision. Existing databases receive only
unapplied ordered forward migrations. A transaction-scoped advisory lock
serializes concurrent container startups. ``--dry-run`` performs discovery and
reports safe target metadata without opening a database connection.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import asyncpg

from app.config import database_log_meta, get_settings
from scripts.apply_migrations import discover_migrations
from scripts.apply_schema import resolve_schema_path

logger = logging.getLogger(__name__)

BOOTSTRAP_LOCK_ID = 1_146_323_076
SCHEMA_SENTINEL = "players"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the database bootstrap command line.

    Args:
        argv: Optional arguments excluding the process name.

    Returns:
        Parsed arguments containing the non-mutating ``dry_run`` flag.
    """
    logger.info("parse_args called argv_length=%s", 0 if argv is None else len(argv))
    parser = argparse.ArgumentParser(
        description="Bootstrap a fresh database or apply pending migrations.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect schema and migrations without connecting to Postgres.",
    )
    return parser.parse_args(argv)


async def bootstrap_database(*, dry_run: bool = False) -> dict[str, object]:
    """Create the latest schema or migrate an existing database.

    Args:
        dry_run: When true, read local SQL files but perform no network or
            database I/O.

    Returns:
        Metadata-only summary describing the selected action and revisions.

    Side effects:
        In live mode, opens one Postgres connection and transactionally applies
        schema DDL or pending migration DDL. Never logs credentials.
    """
    settings = get_settings()
    schema_path = resolve_schema_path()
    migrations = discover_migrations()
    summary: dict[str, object] = {
        "dry_run": dry_run,
        "database": database_log_meta(settings.database_url),
        "schema": schema_path.name,
        "migrations": [path.name for path in migrations],
    }
    logger.info(
        "bootstrap_database called dry_run=%s schema=%s migration_count=%s database=%s",
        dry_run,
        schema_path.name,
        len(migrations),
        summary["database"],
    )
    if dry_run:
        if not schema_path.read_text(encoding="utf-8").strip():
            raise ValueError("schema file is empty")
        summary["action"] = "inspect"
        return summary

    connection = await asyncpg.connect(settings.database_url)
    try:
        async with connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock($1)",
                BOOTSTRAP_LOCK_ID,
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            schema_exists = bool(
                await connection.fetchval(
                    "SELECT to_regclass($1) IS NOT NULL",
                    f"public.{SCHEMA_SENTINEL}",
                )
            )
            if not schema_exists:
                await _install_fresh_schema(connection, schema_path, migrations)
                summary["action"] = "schema"
                summary["applied"] = [path.name for path in migrations]
            else:
                applied = await _apply_pending_migrations(connection, migrations)
                summary["action"] = "migrations"
                summary["applied"] = applied
    finally:
        await connection.close()
    logger.info(
        "bootstrap_database completed action=%s applied_count=%s",
        summary["action"],
        len(summary["applied"]),  # type: ignore[arg-type]
    )
    return summary


async def _install_fresh_schema(
    connection: asyncpg.Connection,
    schema_path: Path,
    migrations: list[Path],
) -> None:
    """Install current schema and mark included migrations as applied.

    Args:
        connection: Open connection inside the bootstrap transaction.
        schema_path: Latest canonical schema SQL.
        migrations: Ordered migration files already represented by that schema.

    Side effects:
        Executes schema DDL and inserts migration revision rows.
    """
    sql_text = schema_path.read_text(encoding="utf-8")
    logger.info(
        "_install_fresh_schema called schema_bytes=%s migration_count=%s",
        len(sql_text.encode("utf-8")),
        len(migrations),
    )
    await connection.execute(sql_text)
    for path in migrations:
        await connection.execute(
            """
            INSERT INTO schema_migrations (filename)
            VALUES ($1)
            ON CONFLICT (filename) DO NOTHING
            """,
            path.name,
        )


async def _apply_pending_migrations(
    connection: asyncpg.Connection,
    migrations: list[Path],
) -> list[str]:
    """Apply migration files absent from ``schema_migrations``.

    Args:
        connection: Open connection inside the bootstrap transaction.
        migrations: Ordered migration paths.

    Returns:
        Filenames applied during this invocation.

    Side effects:
        Executes pending DDL and records each completed filename.
    """
    logger.info("_apply_pending_migrations called migration_count=%s", len(migrations))
    existing = set(
        await connection.fetchval(
            "SELECT array_agg(filename) FROM schema_migrations",
        )
        or []
    )
    applied: list[str] = []
    for path in migrations:
        if path.name in existing:
            continue
        sql_text = path.read_text(encoding="utf-8")
        logger.info(
            "_apply_pending_migrations applying filename=%s sql_bytes=%s",
            path.name,
            len(sql_text.encode("utf-8")),
        )
        await connection.execute(sql_text)
        await connection.execute(
            "INSERT INTO schema_migrations (filename) VALUES ($1)",
            path.name,
        )
        applied.append(path.name)
    return applied


async def main(argv: list[str] | None = None) -> int:
    """Run database bootstrap and return a process exit code.

    Args:
        argv: Optional arguments excluding the process name.

    Returns:
        Zero after successful inspection, schema creation, or migration.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = parse_args(argv)
    await bootstrap_database(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
