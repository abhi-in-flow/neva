"""Database bootstrap and assertion helpers for isolated Wave 2 E2E runs."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from typing import Any

import asyncpg

from tests.integration.config import Wave2E2EConfig
from tests.integration.process import build_child_env

LOGGER = logging.getLogger(__name__)
SCHEMA_SENTINEL = "players"
EMPTY_TABLE_COUNTS = (
    "players",
    "pairs",
    "matchmaking_queue",
    "decks",
    "cards",
    "turns",
    "jobs",
    "records",
    "metrics_counters",
    "api_calls",
    "speaker_audio_fingerprints",
)


async def postgres_reachable(database_url: str, *, timeout_s: float = 3.0) -> bool:
    """Return whether Postgres accepts a read-only connection.

    Args:
        database_url: Guarded target DSN.
        timeout_s: Connection timeout.

    Returns:
        Whether ``SELECT 1`` succeeds.
    """
    LOGGER.info("postgres_reachable called timeout_s=%s", timeout_s)
    try:
        connection = await asyncio.wait_for(asyncpg.connect(database_url), timeout=timeout_s)
    except Exception as error:
        LOGGER.info("postgres_reachable failed error_type=%s", type(error).__name__)
        return False
    try:
        await connection.fetchval("SELECT 1")
    finally:
        await connection.close()
    return True


async def schema_exists(database_url: str) -> bool:
    """Return whether the canonical schema sentinel exists.

    Args:
        database_url: Guarded target DSN.

    Returns:
        Whether ``public.players`` exists.
    """
    LOGGER.info("schema_exists called")
    connection = await asyncpg.connect(database_url)
    try:
        return bool(
            await connection.fetchval(
                "SELECT to_regclass($1) IS NOT NULL",
                f"public.{SCHEMA_SENTINEL}",
            )
        )
    finally:
        await connection.close()


async def table_counts(database_url: str) -> dict[str, int]:
    """Return row counts for mutable E2E tables.

    Args:
        database_url: Guarded target DSN.

    Returns:
        Table names mapped to counts, or ``-1`` when missing.
    """
    LOGGER.info("table_counts called")
    connection = await asyncpg.connect(database_url)
    counts: dict[str, int] = {}
    try:
        for table in EMPTY_TABLE_COUNTS:
            exists = await connection.fetchval("SELECT to_regclass($1)", f"public.{table}")
            counts[table] = (
                int(await connection.fetchval(f"SELECT COUNT(*)::int FROM {table}"))
                if exists is not None
                else -1
            )
    finally:
        await connection.close()
    return counts


async def validate_isolated_empty_target(database_url: str) -> None:
    """Reject an occupied isolated target before E2E mutation.

    Args:
        database_url: Guarded target DSN.

    Raises:
        RuntimeError: When a mutable table contains rows.
    """
    LOGGER.info("validate_isolated_empty_target called")
    counts = await table_counts(database_url)
    if counts.get(SCHEMA_SENTINEL, -1) < 0:
        return
    occupied = {table: count for table, count in counts.items() if count > 0}
    if occupied:
        raise RuntimeError(f"isolated database is not empty: {occupied}")


def bootstrap_database_subprocess(config: Wave2E2EConfig) -> dict[str, object]:
    """Bootstrap the target in a fresh process.

    Args:
        config: Guarded E2E configuration.

    Returns:
        Child exit metadata.

    Raises:
        RuntimeError: When bootstrap fails.
    """
    LOGGER.info("bootstrap_database_subprocess called database_name=%s", config.database_name)
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.bootstrap_database"],
        cwd=config.repo_root,
        env=build_child_env(config),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"database bootstrap failed exit_code={completed.returncode} "
            f"stderr_tail={completed.stderr[-500:]}"
        )
    return {"returncode": completed.returncode, "stdout": completed.stdout.strip()}


async def prepare_database_target(config: Wave2E2EConfig) -> dict[str, object]:
    """Bootstrap and prove that the target is empty.

    Args:
        config: Guarded E2E configuration.

    Returns:
        Bootstrap and table-count evidence.
    """
    LOGGER.info("prepare_database_target called database_name=%s", config.database_name)
    existed_before = await schema_exists(config.database_url)
    bootstrap = bootstrap_database_subprocess(config)
    await validate_isolated_empty_target(config.database_url)
    return {
        "schema_existed_before": existed_before,
        "bootstrap": bootstrap,
        "table_counts": await table_counts(config.database_url),
    }


async def fetch_job_count(database_url: str, *, kind: str, turn_id: str) -> int:
    """Count durable jobs for one turn.

    Args:
        database_url: Guarded target DSN.
        kind: Job kind.
        turn_id: Turn UUID string.

    Returns:
        Matching job count.
    """
    connection = await asyncpg.connect(database_url)
    try:
        value = await connection.fetchval(
            """
            SELECT COUNT(*)::int FROM jobs
            WHERE kind=$1 AND payload->>'turn_id'=$2
            """,
            kind,
            turn_id,
        )
        return int(value or 0)
    finally:
        await connection.close()


async def fetch_record_snapshot(database_url: str, turn_id: str) -> dict[str, Any] | None:
    """Fetch one canonical record snapshot.

    Args:
        database_url: Guarded target DSN.
        turn_id: Turn UUID string.

    Returns:
        Parsed record mapping or ``None``.
    """
    connection = await asyncpg.connect(database_url)
    try:
        row = await connection.fetchrow(
            """
            SELECT turn_id::text, training_eligible, shard_file, golden
            FROM records WHERE turn_id=$1::uuid
            """,
            turn_id,
        )
    finally:
        await connection.close()
    if row is None:
        return None
    golden = row["golden"]
    if isinstance(golden, str):
        golden = json.loads(golden)
    return {
        "turn_id": row["turn_id"],
        "training_eligible": bool(row["training_eligible"]),
        "shard_file": row["shard_file"],
        "golden": golden,
    }


async def fetch_turn_card_id(database_url: str, turn_id: str) -> str:
    """Return the card UUID assigned to a turn.

    Args:
        database_url: Guarded target DSN.
        turn_id: Turn UUID string.

    Returns:
        Card UUID string.

    Raises:
        RuntimeError: When no card is assigned.
    """
    connection = await asyncpg.connect(database_url)
    try:
        value = await connection.fetchval(
            "SELECT card_id::text FROM turns WHERE id=$1::uuid",
            turn_id,
        )
    finally:
        await connection.close()
    if not value:
        raise RuntimeError(f"turn card missing turn_id={turn_id}")
    return str(value)


async def fetch_turn_quality(database_url: str, turn_id: str) -> dict[str, object] | None:
    """Return parsed triage quality for one turn.

    Args:
        database_url: Guarded target DSN.
        turn_id: Turn UUID string.

    Returns:
        Parsed quality object or ``None``.
    """
    connection = await asyncpg.connect(database_url)
    try:
        value = await connection.fetchval(
            "SELECT quality FROM turns WHERE id=$1::uuid",
            turn_id,
        )
    finally:
        await connection.close()
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)
