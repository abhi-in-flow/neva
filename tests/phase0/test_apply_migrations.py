"""Non-destructive tests for ordered contract migration discovery.

These tests prove the migration CLI discovers SQL in deterministic order and
that dry-run mode never connects to Postgres. They exercise the operator-facing
path without changing the development database or runtime data.
"""

from __future__ import annotations

import logging

import pytest

from scripts.apply_migrations import apply_migrations, discover_migrations, parse_args

logger = logging.getLogger(__name__)


def test_parse_args_supports_dry_run() -> None:
    """Confirm the migration CLI accepts explicit dry-run mode."""
    logger.info("test_parse_args_supports_dry_run called")
    assert parse_args(["--dry-run"]).dry_run is True
    assert parse_args([]).dry_run is False


def test_discover_migrations_is_ordered() -> None:
    """Confirm migration SQL files are present and filename-sorted."""
    logger.info("test_discover_migrations_is_ordered called")
    migrations = discover_migrations()
    assert migrations
    assert migrations == sorted(migrations)
    assert all(path.suffix == ".sql" for path in migrations)


@pytest.mark.asyncio
async def test_dry_run_never_connects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return migration names without opening a database connection.

    Args:
        monkeypatch: Replaces ``asyncpg.connect`` with a failing sentinel.
    """
    logger.info("test_dry_run_never_connects called")

    async def _forbidden_connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry-run must not connect to Postgres")

    monkeypatch.setattr("scripts.apply_migrations.asyncpg.connect", _forbidden_connect)
    names = await apply_migrations(dry_run=True)
    assert "0002_triage_package_protocol.sql" in names
