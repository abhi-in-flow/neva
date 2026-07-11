"""Fail-closed source and post-restore integrity tests."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import pytest

from scripts.ops.cli import main
from scripts.ops.health import ComposeHealthError, require_service_healthy
from scripts.ops.integrity import (
    BackupIntegrityError,
    build_restore_report,
    load_manifest,
    validate_post_restore_database,
    verify_backup_source,
    verify_restored_runtime,
)
from scripts.ops.manifest import sha256_file

LOGGER = logging.getLogger(__name__)


def test_verify_backup_source_accepts_complete_backup(valid_backup: Path) -> None:
    """Accept a manifest whose exact artifacts, gzip, and counts verify."""
    LOGGER.info("test_verify_backup_source_accepts_complete_backup called")
    summary = verify_backup_source(valid_backup)
    assert summary["checked_file_count"] == 3
    assert summary["runtime_counts"] == {"audio": 1, "decks": 0, "corpus": 1}
    LOGGER.info("test_verify_backup_source_accepts_complete_backup completed")


def test_verify_backup_source_refuses_corrupt_checksum(valid_backup: Path) -> None:
    """Refuse a backup when a protected runtime file changes."""
    LOGGER.info("test_verify_backup_source_refuses_corrupt_checksum called")
    (valid_backup / "runtime" / "audio" / "turn-1.flac").write_bytes(b"corrupt")
    with pytest.raises(BackupIntegrityError, match="checksum mismatch"):
        verify_backup_source(valid_backup)
    LOGGER.info("test_verify_backup_source_refuses_corrupt_checksum completed")


def test_verify_backup_source_refuses_corrupt_gzip(valid_backup: Path) -> None:
    """Refuse a checksum-consistent fixture whose gzip stream is invalid."""
    LOGGER.info("test_verify_backup_source_refuses_corrupt_gzip called")
    dump_path = valid_backup / "postgres" / "dump.sql.gz"
    dump_path.write_bytes(b"not-gzip")
    checksum_path = valid_backup / "checksums.sha256"
    lines = checksum_path.read_text(encoding="utf-8").splitlines()
    updated = [
        f"{sha256_file(dump_path)}  postgres/dump.sql.gz"
        if line.endswith("  postgres/dump.sql.gz")
        else line
        for line in lines
    ]
    checksum_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    with pytest.raises(BackupIntegrityError, match="gzip stream is corrupt"):
        verify_backup_source(valid_backup)
    LOGGER.info("test_verify_backup_source_refuses_corrupt_gzip completed")


def test_verify_backup_source_refuses_secret_file(valid_backup: Path) -> None:
    """Refuse secret files even when they are absent from the checksum index."""
    LOGGER.info("test_verify_backup_source_refuses_secret_file called")
    (valid_backup / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    with pytest.raises(BackupIntegrityError, match="secret file"):
        verify_backup_source(valid_backup)
    LOGGER.info("test_verify_backup_source_refuses_secret_file completed")


def test_restored_runtime_preserves_marker_and_matches(valid_backup: Path, tmp_path: Path) -> None:
    """Verify restored bytes while preserving isolated control metadata."""
    LOGGER.info("test_restored_runtime_preserves_marker_and_matches called")
    target = tmp_path / "isolated-data"
    target.mkdir()
    marker = target / ".neva-isolated"
    marker.write_text("control\n", encoding="utf-8")
    for source_path in (valid_backup / "runtime").rglob("*"):
        relative = source_path.relative_to(valid_backup / "runtime")
        target_path = target / relative
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
    manifest = load_manifest(valid_backup)
    counts = verify_restored_runtime(
        source=valid_backup,
        target_data_dir=target,
        manifest=manifest,
    )
    assert counts == manifest["source"]["runtime_counts"]
    assert marker.read_text(encoding="utf-8") == "control\n"
    LOGGER.info("test_restored_runtime_preserves_marker_and_matches completed")


def test_database_verification_refuses_count_or_constraint_mismatch(valid_backup: Path) -> None:
    """Fail closed for changed row counts or unvalidated constraints."""
    LOGGER.info("test_database_verification_refuses_count_or_constraint_mismatch called")
    expected = load_manifest(valid_backup)["database_counts"]
    changed = {**expected, "records": expected["records"] + 1}
    with pytest.raises(BackupIntegrityError, match="counts"):
        validate_post_restore_database(
            expected_counts=expected,
            actual_counts=changed,
            invalid_constraint_count=0,
        )
    with pytest.raises(BackupIntegrityError, match="constraints"):
        validate_post_restore_database(
            expected_counts=expected,
            actual_counts=expected,
            invalid_constraint_count=1,
        )
    LOGGER.info("test_database_verification_refuses_count_or_constraint_mismatch completed")


def test_restore_report_is_metadata_only(valid_backup: Path) -> None:
    """Construct a report containing counts and RTO but no payload rows."""
    LOGGER.info("test_restore_report_is_metadata_only called")
    manifest = load_manifest(valid_backup)
    report = build_restore_report(
        manifest=manifest,
        runtime_counts=manifest["source"]["runtime_counts"],
        database_counts=manifest["database_counts"],
        invalid_constraint_count=0,
        elapsed_seconds=12.3456,
    )
    encoded = json.dumps(report)
    assert report["status"] == "pass"
    assert report["elapsed_seconds"] == 12.346
    assert "utterance_id" not in encoded
    LOGGER.info("test_restore_report_is_metadata_only completed")


def test_cli_post_restore_writes_verified_report(valid_backup: Path, tmp_path: Path) -> None:
    """Run post-restore gates and write the expected metadata-only report."""
    LOGGER.info("test_cli_post_restore_writes_verified_report called")
    target = tmp_path / "isolated-data"
    shutil.copytree(valid_backup / "runtime", target)
    (target / ".neva-isolated").write_text("control\n", encoding="utf-8")
    report_path = tmp_path / "reports" / "restore.json"
    database_counts = load_manifest(valid_backup)["database_counts"]
    exit_code = main(
        [
            "verify-restore",
            "--source",
            str(valid_backup),
            "--data-dir",
            str(target),
            "--actual-database-counts-json",
            json.dumps(database_counts),
            "--invalid-constraint-count",
            "0",
            "--elapsed-seconds",
            "7.25",
            "--report",
            str(report_path),
        ],
    )
    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["elapsed_seconds"] == 7.25
    LOGGER.info("test_cli_post_restore_writes_verified_report completed")


def test_worker_heartbeat_requires_compose_healthy() -> None:
    """Accept healthy worker heartbeat state and reject merely running state."""
    LOGGER.info("test_worker_heartbeat_requires_compose_healthy called")
    healthy = json.dumps({"Service": "worker", "State": "running", "Health": "healthy"})
    assert require_service_healthy(healthy, "worker")["health"] == "healthy"
    wedged = json.dumps({"Service": "worker", "State": "running", "Health": "unhealthy"})
    with pytest.raises(ComposeHealthError, match="not healthy"):
        require_service_healthy(wedged, "worker")
    LOGGER.info("test_worker_heartbeat_requires_compose_healthy completed")
