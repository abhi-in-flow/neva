"""Apply the Phase 0 Postgres schema to the configured development database.

Reads ``contracts/schema.sql`` and executes it against ``Settings.database_url``.
Supports ``--dry-run`` so operators can validate the schema file and target
metadata without mutating the database. This script is an operational tool for
local Wave 0 setup; it is not imported by the FastAPI request path.
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

SCHEMA_RELATIVE_PATH = Path("contracts") / "schema.sql"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for schema application.

    Args:
        argv: Optional argument vector; defaults to ``sys.argv[1:]``.

    Returns:
        Parsed namespace containing the ``dry_run`` flag.
    """
    logger.info("parse_args called argv_length=%s", 0 if argv is None else len(argv))
    parser = argparse.ArgumentParser(
        description="Apply contracts/schema.sql to the configured Postgres database.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate schema file and log target metadata without executing SQL.",
    )
    return parser.parse_args(argv)


def resolve_schema_path() -> Path:
    """Resolve the absolute path to the frozen schema contract.

    Returns:
        Absolute path to ``contracts/schema.sql`` relative to the repository root.

    Side effects:
        None beyond path resolution and INFO logging.
    """
    schema_path = Path(__file__).resolve().parents[1] / SCHEMA_RELATIVE_PATH
    logger.info("resolve_schema_path called path=%s", schema_path)
    return schema_path


async def apply_schema(*, dry_run: bool = False) -> dict[str, object]:
    """Apply or dry-run the Phase 0 schema against the configured database.

    Args:
        dry_run: When True, read and validate the schema file and report the
            target database metadata without opening a mutating connection
            execute. When False, connect and execute the full schema SQL.

    Returns:
        A summary dict with schema path, byte length, dry-run flag, and safe
        database metadata.

    Side effects:
        In dry-run mode, only reads the schema file. In apply mode, executes
        DDL against Postgres. Never logs credentials or the raw DSN.
    """
    settings = get_settings()
    schema_path = resolve_schema_path()
    sql_text = schema_path.read_text(encoding="utf-8")
    meta = {
        "schema_path": str(schema_path),
        "schema_bytes": len(sql_text.encode("utf-8")),
        "dry_run": dry_run,
        "database": database_log_meta(settings.database_url),
    }
    logger.info(
        "apply_schema called dry_run=%s schema_bytes=%s database=%s",
        dry_run,
        meta["schema_bytes"],
        meta["database"],
    )
    if dry_run:
        if not sql_text.strip():
            raise ValueError("schema file is empty")
        logger.info("apply_schema dry-run complete; no database changes")
        return meta

    connection = await asyncpg.connect(settings.database_url)
    try:
        await connection.execute(sql_text)
    finally:
        await connection.close()
    logger.info("apply_schema completed database changes applied")
    return meta


async def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for schema application.

    Args:
        argv: Optional CLI arguments for testing; defaults to process argv.

    Returns:
        Process exit code: ``0`` on success.

    Side effects:
        Configures logging, then dry-runs or applies the schema as requested.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = parse_args(argv)
    logger.info("main called dry_run=%s", args.dry_run)
    await apply_schema(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
