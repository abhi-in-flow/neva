"""Phase 0 schema script dry-run smoke tests.

Exercises ``scripts.apply_schema`` in dry-run mode so the schema file is
validated without applying DDL to the development database.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scripts.apply_schema import apply_schema, parse_args, resolve_schema_path

logger = logging.getLogger(__name__)


def test_parse_args_dry_run_flag() -> None:
    """Parse ``--dry-run`` from the schema script CLI.

    Returns:
        None.
    """
    logger.info("test_parse_args_dry_run_flag called")
    args = parse_args(["--dry-run"])
    assert args.dry_run is True
    args_default = parse_args([])
    assert args_default.dry_run is False
    logger.info("test_parse_args_dry_run_flag completed")


def test_resolve_schema_path_points_at_contract() -> None:
    """Confirm the schema path resolves to the frozen contract file.

    Returns:
        None.
    """
    logger.info("test_resolve_schema_path_points_at_contract called")
    path = resolve_schema_path()
    assert path.name == "schema.sql"
    assert path.parent.name == "contracts"
    assert path.is_file()
    logger.info("test_resolve_schema_path_points_at_contract completed path=%s", path)


@pytest.mark.asyncio
async def test_apply_schema_dry_run_does_not_require_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Dry-run reads the schema and returns metadata without connecting.

    Args:
        monkeypatch: Used to fail the test if ``asyncpg.connect`` is called.
        tmp_path: Unused placeholder keeping fixture style consistent.

    Returns:
        None.

    Side effects:
        Reads ``contracts/schema.sql`` only. Patches ``asyncpg.connect`` to
        raise if invoked.
    """
    logger.info(
        "test_apply_schema_dry_run_does_not_require_database called tmp=%s",
        tmp_path,
    )

    async def _forbidden_connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry-run must not open a database connection")

    monkeypatch.setattr("scripts.apply_schema.asyncpg.connect", _forbidden_connect)
    summary = await apply_schema(dry_run=True)
    assert summary["dry_run"] is True
    assert int(summary["schema_bytes"]) > 0
    assert Path(str(summary["schema_path"])).is_file()
    logger.info(
        "test_apply_schema_dry_run_does_not_require_database completed "
        "schema_bytes=%s",
        summary["schema_bytes"],
    )
