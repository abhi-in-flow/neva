"""Manifest and checksum tests for Wave 2 recovery operations."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from scripts.ops.manifest import (
    build_manifest,
    collect_checksum_lines,
    count_runtime_entries,
    should_include_in_backup,
    write_checksums,
    write_manifest,
)

LOGGER = logging.getLogger(__name__)


def test_should_exclude_secret_files() -> None:
    """Exclude .env variants from backup inclusion checks."""
    LOGGER.info("test_should_exclude_secret_files called")
    assert should_include_in_backup(Path(".env")) is False
    assert should_include_in_backup(Path("nested/.env.staging")) is False
    assert should_include_in_backup(Path("audio/turn.webm")) is True
    LOGGER.info("test_should_exclude_secret_files completed")


def test_count_runtime_entries_excludes_env(runtime_tree: Path) -> None:
    """Count runtime files without treating .env as backup payload."""
    LOGGER.info("test_count_runtime_entries_excludes_env called")
    counts = count_runtime_entries(runtime_tree)
    assert counts == {"audio": 1, "decks": 1, "corpus": 1}
    LOGGER.info("test_count_runtime_entries_excludes_env completed")


def test_build_manifest_is_metadata_only(runtime_tree: Path, tmp_path: Path) -> None:
    """Ensure manifests contain metadata only and no secret payloads."""
    LOGGER.info("test_build_manifest_is_metadata_only called")
    destination = tmp_path / "backups" / "neva-test"
    manifest = build_manifest(
        backup_id="test-backup",
        data_dir=runtime_tree,
        destination=destination,
        database_meta={"host": "localhost", "port": 5432, "database": "dialect_factory"},
        dry_run=True,
        runtime_counts={"audio": 1, "decks": 1, "corpus": 1},
    )
    encoded = json.dumps(manifest)
    assert "GEMINI_API_KEY" not in encoded
    assert "secret" not in encoded
    assert manifest["excluded"] == [".env*"]
    LOGGER.info("test_build_manifest_is_metadata_only completed")


def test_aggregate_checksum_manifest(runtime_tree: Path, tmp_path: Path) -> None:
    """Write aggregate checksum lines for postgres and runtime artifacts."""
    LOGGER.info("test_aggregate_checksum_manifest called")
    backup_root = tmp_path / "backup"
    postgres_dir = backup_root / "postgres"
    runtime_dir = backup_root / "runtime"
    postgres_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    (postgres_dir / "dump.sql.gz").write_bytes(b"gz-bytes")
    (runtime_dir / "audio").mkdir()
    (runtime_dir / "audio" / "turn-1.webm").write_bytes(b"webm-bytes")

    lines = collect_checksum_lines(postgres_dir)
    lines.extend(
        collect_checksum_lines(runtime_dir, prefix=Path("runtime")),
    )
    write_checksums(backup_root, sorted(lines))
    write_manifest(
        backup_root,
        build_manifest(
            backup_id="checksum-test",
            data_dir=runtime_tree,
            destination=backup_root,
            database_meta={"host": "localhost", "port": 5432, "database": "dialect_factory"},
            dry_run=False,
            runtime_counts=count_runtime_entries(runtime_tree),
        ),
    )

    checksum_text = (backup_root / "checksums.sha256").read_text(encoding="utf-8")
    assert "dump.sql.gz" in checksum_text
    assert "runtime/audio/turn-1.webm" in checksum_text
    assert ".env" not in checksum_text
    LOGGER.info("test_aggregate_checksum_manifest completed")
