"""Shared fixtures for Wave 2 recovery operations tests."""

from __future__ import annotations

import gzip
import logging
from pathlib import Path

import pytest

from scripts.ops.manifest import (
    build_manifest,
    collect_checksum_lines,
    count_runtime_entries,
    write_checksums,
    write_manifest,
)

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def isolated_data_dir(tmp_path: Path) -> Path:
    """Create an isolated runtime-data directory with the required marker file.

    Args:
        tmp_path: Pytest temporary directory root.

    Returns:
        Path to a DATA_DIR marked safe for restore verification tests.
    """
    data_dir = tmp_path / "isolated-data"
    data_dir.mkdir()
    (data_dir / ".neva-isolated").write_text("wave-2 integration\n", encoding="utf-8")
    LOGGER.info("isolated_data_dir fixture created path=%s", data_dir)
    return data_dir


@pytest.fixture
def runtime_tree(tmp_path: Path) -> Path:
    """Create a minimal contract runtime tree for manifest and backup tests.

    Args:
        tmp_path: Pytest temporary directory root.

    Returns:
        Path to a DATA_DIR containing audio, decks, corpus, and a secret file.
    """
    data_dir = tmp_path / "live-data"
    (data_dir / "audio").mkdir(parents=True)
    (data_dir / "decks" / "deck-a").mkdir(parents=True)
    (data_dir / "corpus").mkdir(parents=True)
    (data_dir / "audio" / "turn-1.webm").write_bytes(b"webm-bytes")
    (data_dir / "decks" / "deck-a" / "card-1.png").write_bytes(b"png-bytes")
    (data_dir / "corpus" / "shard_0001.jsonl").write_text('{"id":"1"}\n', encoding="utf-8")
    (data_dir / ".env").write_text("GEMINI_API_KEY=secret\n", encoding="utf-8")
    LOGGER.info("runtime_tree fixture created path=%s", data_dir)
    return data_dir


@pytest.fixture
def valid_backup(tmp_path: Path) -> Path:
    """Create a fully checksummed, gzip-valid version-2 backup fixture.

    Args:
        tmp_path: Pytest temporary directory root.

    Returns:
        Path to a backup accepted by source-integrity verification.
    """
    backup = tmp_path / "valid-backup"
    postgres = backup / "postgres"
    runtime = backup / "runtime"
    postgres.mkdir(parents=True)
    (runtime / "audio").mkdir(parents=True)
    (runtime / "decks").mkdir()
    (runtime / "corpus").mkdir()
    with gzip.open(postgres / "dump.sql.gz", "wb") as handle:
        handle.write(b"CREATE TABLE test_table(id integer);\n")
    (runtime / "audio" / "turn-1.flac").write_bytes(b"flac-fixture")
    (runtime / "corpus" / "shard_0001.jsonl").write_text(
        '{"utterance_id":"fixture"}\n',
        encoding="utf-8",
    )
    checksum_lines = collect_checksum_lines(postgres, prefix=Path("postgres"))
    checksum_lines.extend(collect_checksum_lines(runtime, prefix=Path("runtime")))
    database_counts = {
        "players": 2,
        "pairs": 1,
        "matchmaking_queue": 0,
        "decks": 1,
        "cards": 1,
        "turns": 1,
        "jobs": 1,
        "records": 1,
        "metrics_counters": 3,
        "api_calls": 0,
        "speaker_audio_fingerprints": 1,
        "worker_heartbeats": 1,
    }
    manifest = build_manifest(
        backup_id="valid-backup",
        data_dir=tmp_path / "source-data",
        destination=backup,
        database_meta={"host": "localhost", "port": 5432, "database": "dialect_factory"},
        dry_run=False,
        runtime_counts=count_runtime_entries(runtime),
        database_counts=database_counts,
        expected_file_count=len(checksum_lines),
    )
    write_manifest(backup, manifest)
    write_checksums(backup, sorted(checksum_lines))
    LOGGER.info("valid_backup fixture created path=%s", backup)
    return backup
