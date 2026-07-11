"""Restore refusal tests for Wave 2 recovery operations."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scripts.ops.cli import main
from scripts.ops.paths import OpsPathError, validate_restore_target

LOGGER = logging.getLogger(__name__)


def test_isolated_restore_target_accepts_marked_paths(isolated_data_dir: Path, tmp_path: Path) -> None:
    """Accept restore targets that satisfy all isolation requirements."""
    LOGGER.info("test_isolated_restore_target_accepts_marked_paths called")
    validate_restore_target(
        data_dir=isolated_data_dir,
        database_url="postgresql://dialect:pw@localhost:5432/dialect_factory_isolated",
        compose_project="neva_isolated",
        isolated_env="1",
        live_data_dir=tmp_path / "live-data",
        live_database_url="postgresql://dialect:pw@localhost:5432/dialect_factory",
    )
    LOGGER.info("test_isolated_restore_target_accepts_marked_paths completed")


def test_cli_validate_restore_accepts_isolated_backup(
    isolated_data_dir: Path,
    valid_backup: Path,
    tmp_path: Path,
) -> None:
    """CLI restore validation succeeds for isolated targets with a manifest."""
    LOGGER.info("test_cli_validate_restore_accepts_isolated_backup called")
    exit_code = main(
        [
            "validate-restore",
            "--source",
            str(valid_backup),
            "--data-dir",
            str(isolated_data_dir),
            "--database-url",
            "postgresql://dialect:secret@localhost:5432/dialect_factory_isolated",
            "--compose-project",
            "neva_isolated",
            "--postgres-db",
            "dialect_factory_isolated",
            "--isolated-env",
            "1",
            "--live-data-dir",
            str(tmp_path / "live-data"),
            "--live-database-url",
            "postgresql://dialect:secret@localhost:5432/dialect_factory",
        ],
    )
    assert exit_code == 0
    LOGGER.info("test_cli_validate_restore_accepts_isolated_backup completed")


def test_restore_refuses_populated_destination(isolated_data_dir: Path) -> None:
    """Refuse restore when runtime data already exists beside the marker file."""
    LOGGER.info("test_restore_refuses_populated_destination called")
    (isolated_data_dir / "corpus").mkdir()
    with pytest.raises(OpsPathError, match="already contains data"):
        from scripts.ops.paths import validate_restore_destination_empty

        validate_restore_destination_empty(isolated_data_dir)
    LOGGER.info("test_restore_refuses_populated_destination completed")


def test_cli_refuses_nonempty_target_database() -> None:
    """Fail closed when preflight reports any existing user table."""
    LOGGER.info("test_cli_refuses_nonempty_target_database called")
    assert main(["validate-db-empty", "--user-table-count", "1"]) == 2
    assert main(["validate-db-empty", "--user-table-count", "0"]) == 0
    assert main(["validate-db-empty", "--user-table-count", "not-a-number"]) == 2
    LOGGER.info("test_cli_refuses_nonempty_target_database completed")
