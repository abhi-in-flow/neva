"""Path safety tests for Wave 2 recovery operations."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scripts.ops.paths import (
    OpsPathError,
    is_path_inside,
    validate_backup_destination,
    validate_restore_destination_empty,
    validate_restore_target,
)

LOGGER = logging.getLogger(__name__)


def test_backup_destination_must_be_outside_data_dir(tmp_path: Path) -> None:
    """Refuse backup destinations nested under DATA_DIR."""
    LOGGER.info("test_backup_destination_must_be_outside_data_dir called")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    nested = data_dir / "backups" / "neva-test"
    with pytest.raises(OpsPathError, match="outside DATA_DIR"):
        validate_backup_destination(nested, data_dir, exists=False)
    LOGGER.info("test_backup_destination_must_be_outside_data_dir completed")


def test_backup_destination_refuses_existing_path(tmp_path: Path) -> None:
    """Refuse overwrite when the backup destination already exists."""
    LOGGER.info("test_backup_destination_refuses_existing_path called")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    destination = tmp_path / "backups" / "neva-test"
    destination.mkdir(parents=True)
    with pytest.raises(OpsPathError, match="already exists"):
        validate_backup_destination(destination, data_dir, exists=True)
    LOGGER.info("test_backup_destination_refuses_existing_path completed")


def test_is_path_inside_detects_nested_paths(tmp_path: Path) -> None:
    """Detect when a child path resolves under a parent directory."""
    LOGGER.info("test_is_path_inside_detects_nested_paths called")
    parent = tmp_path / "data"
    child = parent / "audio" / "clip.webm"
    child.parent.mkdir(parents=True)
    assert is_path_inside(child, parent) is True
    assert is_path_inside(tmp_path / "other", parent) is False
    LOGGER.info("test_is_path_inside_detects_nested_paths completed")


def test_restore_refuses_live_data_dir(tmp_path: Path) -> None:
    """Refuse restore verification into the live development DATA_DIR."""
    LOGGER.info("test_restore_refuses_live_data_dir called")
    live_data = tmp_path / "data"
    live_data.mkdir()
    (live_data / ".neva-isolated").write_text("marker\n", encoding="utf-8")
    with pytest.raises(OpsPathError, match="live development path"):
        validate_restore_target(
            data_dir=live_data,
            database_url="postgresql://dialect:pw@localhost:5432/dialect_factory_isolated",
            compose_project="neva_isolated",
            isolated_env="1",
            live_data_dir=live_data,
        )
    LOGGER.info("test_restore_refuses_live_data_dir completed")


def test_restore_requires_isolated_env_and_marker(isolated_data_dir: Path) -> None:
    """Require NEVA_OPS_ISOLATED and the marker file for restore targets."""
    LOGGER.info("test_restore_requires_isolated_env_and_marker called")
    with pytest.raises(OpsPathError, match="NEVA_OPS_ISOLATED"):
        validate_restore_target(
            data_dir=isolated_data_dir,
            database_url="postgresql://dialect:pw@localhost:5432/dialect_factory_isolated",
            compose_project="neva_isolated",
            isolated_env="",
        )

    bare_dir = isolated_data_dir.parent / "no-marker"
    bare_dir.mkdir()
    with pytest.raises(OpsPathError, match="marker file"):
        validate_restore_target(
            data_dir=bare_dir,
            database_url="postgresql://dialect:pw@localhost:5432/dialect_factory_isolated",
            compose_project="neva_isolated",
            isolated_env="1",
            live_data_dir=isolated_data_dir.parent / "live-data",
        )
    LOGGER.info("test_restore_requires_isolated_env_and_marker completed")


def test_restore_refuses_live_database_name(isolated_data_dir: Path, tmp_path: Path) -> None:
    """Refuse restore when the database name is not isolated."""
    LOGGER.info("test_restore_refuses_live_database_name called")
    with pytest.raises(OpsPathError, match="database name must end with _isolated"):
        validate_restore_target(
            data_dir=isolated_data_dir,
            database_url="postgresql://dialect:pw@localhost:5432/dialect_factory",
            compose_project="neva_isolated",
            isolated_env="1",
            live_data_dir=tmp_path / "live-data",
        )
    LOGGER.info("test_restore_refuses_live_database_name completed")


def test_restore_refuses_non_isolated_compose_project(isolated_data_dir: Path, tmp_path: Path) -> None:
    """Refuse restore when the compose project is not marked isolated."""
    LOGGER.info("test_restore_refuses_non_isolated_compose_project called")
    with pytest.raises(OpsPathError, match="compose project name must end with _isolated"):
        validate_restore_target(
            data_dir=isolated_data_dir,
            database_url="postgresql://dialect:pw@localhost:5432/dialect_factory_isolated",
            compose_project="neva",
            isolated_env="1",
            live_data_dir=tmp_path / "live-data",
        )
    LOGGER.info("test_restore_refuses_non_isolated_compose_project completed")


def test_restore_destination_allows_marker_only(isolated_data_dir: Path) -> None:
    """Allow restore when only the isolated marker file is present."""
    LOGGER.info("test_restore_destination_allows_marker_only called")
    validate_restore_destination_empty(isolated_data_dir)
    (isolated_data_dir / "audio").mkdir()
    with pytest.raises(OpsPathError, match="already contains data"):
        validate_restore_destination_empty(isolated_data_dir)
    LOGGER.info("test_restore_destination_allows_marker_only completed")
