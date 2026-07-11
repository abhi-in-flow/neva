"""Integration-style tests for backup dry-run and CLI safety behavior."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import pytest

from scripts.ops.cli import main

LOGGER = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_SCRIPT = REPO_ROOT / "scripts" / "ops" / "neva-ops.sh"


def test_cli_validate_backup_dry_run_zero_mutation(runtime_tree: Path, tmp_path: Path) -> None:
    """Dry-run validation returns a plan without creating backup directories."""
    LOGGER.info("test_cli_validate_backup_dry_run_zero_mutation called")
    destination = tmp_path / "backups" / "neva-dry-run"
    exit_code = main(
        [
            "validate-backup",
            "--destination",
            str(destination),
            "--data-dir",
            str(runtime_tree),
            "--database-url",
            "postgresql://dialect:secret@localhost:5432/dialect_factory",
            "--dry-run",
        ],
    )
    assert exit_code == 0
    assert not destination.exists()
    LOGGER.info("test_cli_validate_backup_dry_run_zero_mutation completed")


def test_cli_validate_backup_refuses_inside_data_dir(runtime_tree: Path) -> None:
    """CLI validation refuses destinations nested under DATA_DIR."""
    LOGGER.info("test_cli_validate_backup_refuses_inside_data_dir called")
    destination = runtime_tree / "backups" / "nested"
    exit_code = main(
        [
            "validate-backup",
            "--destination",
            str(destination),
            "--data-dir",
            str(runtime_tree),
            "--database-url",
            "postgresql://dialect:secret@localhost:5432/dialect_factory",
            "--dry-run",
        ],
    )
    assert exit_code == 2
    LOGGER.info("test_cli_validate_backup_refuses_inside_data_dir completed")


def test_cli_validate_restore_refuses_live_paths(isolated_data_dir: Path, tmp_path: Path) -> None:
    """CLI restore validation refuses live database names and compose projects."""
    LOGGER.info("test_cli_validate_restore_refuses_live_paths called")
    source = tmp_path / "backup"
    source.mkdir()
    (source / "manifest.json").write_text("{}", encoding="utf-8")

    exit_code = main(
        [
            "validate-restore",
            "--source",
            str(source),
            "--data-dir",
            str(isolated_data_dir),
            "--database-url",
            "postgresql://dialect:secret@localhost:5432/dialect_factory",
            "--compose-project",
            "neva",
            "--postgres-db",
            "dialect_factory",
            "--isolated-env",
            "1",
            "--live-data-dir",
            str(tmp_path / "live-data"),
        ],
    )
    assert exit_code == 2
    LOGGER.info("test_cli_validate_restore_refuses_live_paths completed")


def test_bash_backup_dry_run_zero_mutation(runtime_tree: Path, tmp_path: Path) -> None:
    """Bash backup --dry-run prints a plan and performs zero filesystem mutation."""
    LOGGER.info("test_bash_backup_dry_run_zero_mutation called")
    destination = tmp_path / "backups" / "neva-bash-dry-run"
    result = subprocess.run(
        [str(OPS_SCRIPT), "backup", "--dry-run", "--dest", str(destination)],
        cwd=REPO_ROOT,
        env={
            **subprocess.os.environ,
            "DATA_DIR": str(runtime_tree),
            "NEVA_OPS_BACKUP_ROOT": str(tmp_path / "backups"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert not destination.exists()
    assert "GEMINI_API_KEY" not in result.stdout
    assert "dialect:secret" not in result.stdout
    LOGGER.info("test_bash_backup_dry_run_zero_mutation completed")


def test_bash_restart_dry_run_never_stops_services() -> None:
    """Restart dry-run exits successfully without requiring live services."""
    LOGGER.info("test_bash_restart_dry_run_never_stops_services called")
    result = subprocess.run(
        [str(OPS_SCRIPT), "restart", "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert [step["name"] for step in payload["steps"]] == [
        "stop_worker",
        "stop_api",
        "restart_postgres",
        "wait_postgres",
        "start_api",
        "wait_api",
        "start_worker",
        "wait_worker",
    ]
    LOGGER.info("test_bash_restart_dry_run_never_stops_services completed")


@pytest.mark.skipif(not OPS_SCRIPT.exists(), reason="ops script missing")
def test_bash_script_has_valid_syntax() -> None:
    """Run bash -n syntax check on the recovery entrypoint."""
    LOGGER.info("test_bash_script_has_valid_syntax called")
    result = subprocess.run(
        ["bash", "-n", str(OPS_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    LOGGER.info("test_bash_script_has_valid_syntax completed")
