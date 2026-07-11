"""Metadata-only backup manifests and checksum aggregation for recovery ops.

Builds JSON manifests and SHA-256 checksum files without embedding credentials,
``.env`` contents, or participant payloads. Bash backup/restore scripts call
these helpers so tests can verify aggregate manifest behavior in isolation.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.ops.paths import is_secret_filename

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 2
CHECKSUMS_FILENAME = "checksums.sha256"
MANIFEST_FILENAME = "manifest.json"
POSTGRES_DUMP_RELATIVE = Path("postgres") / "dump.sql.gz"
RUNTIME_ROOT_RELATIVE = Path("runtime")
EXCLUDED_RELATIVE_NAMES = frozenset({".env*"})


def utc_timestamp() -> str:
    """Return a filesystem-safe UTC timestamp for backup directory names.

    Returns:
        Compact ISO-8601 string such as ``20260711T135500Z``.

    Side effects:
        None.
    """
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def should_include_in_backup(relative_path: Path) -> bool:
    """Return False for secret files and other excluded backup paths.

    Args:
        relative_path: Path relative to the backup or runtime root.

    Returns:
        True when the file should appear in checksums and runtime copies.

    Side effects:
        None.
    """
    return not any(is_secret_filename(part) for part in relative_path.parts)


def sha256_file(path: Path) -> str:
    """Compute the SHA-256 digest for a file on disk.

    Args:
        path: Existing regular file to hash.

    Returns:
        Lowercase hex digest string.

    Side effects:
        Reads file bytes from disk; logs path and byte length only.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    logger.info("sha256_file called path=%s byte_length=%s", path, path.stat().st_size)
    return digest.hexdigest()


def collect_checksum_lines(root: Path, *, prefix: Path | None = None) -> list[str]:
    """Collect GNU-style checksum lines for every included file under ``root``.

    Args:
        root: Directory tree to walk recursively.
        prefix: Optional prefix prepended to each relative path in output lines.

    Returns:
        Sorted ``"<digest>  <relative/path>"`` lines.

    Side effects:
        Reads file contents for hashing; never logs digests of secret files
        because those files are excluded up front.
    """
    logger.info("collect_checksum_lines called root=%s prefix=%s", root, prefix)
    lines: list[str] = []
    if not root.exists():
        return lines
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if not should_include_in_backup(relative):
            continue
        display = (prefix / relative).as_posix() if prefix else relative.as_posix()
        lines.append(f"{sha256_file(path)}  {display}")
    return lines


def build_manifest(
    *,
    backup_id: str,
    data_dir: Path,
    destination: Path,
    database_meta: dict[str, Any],
    dry_run: bool,
    runtime_counts: dict[str, int],
    database_counts: dict[str, int] | None = None,
    expected_file_count: int | None = None,
) -> dict[str, Any]:
    """Build a metadata-only manifest describing a backup or dry-run plan.

    Args:
        backup_id: Timestamp or operator-supplied backup identifier.
        data_dir: Source runtime-data directory.
        destination: Intended backup destination root.
        database_meta: Redacted Postgres connection metadata.
        dry_run: Whether the manifest describes a plan instead of materialized
            artifacts.
        runtime_counts: Aggregate counts keyed by runtime subtree name.
        database_counts: Optional metadata-only core-table row counts captured
            immediately before the Postgres dump.
        expected_file_count: Number of dump/runtime artifacts protected by the
            aggregate checksum file.

    Returns:
        JSON-serializable manifest dictionary without credentials or payloads.

    Side effects:
        Logs safe manifest metadata at INFO.
    """
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "backup_id": backup_id,
        "created_at": datetime.now(UTC).isoformat(),
        "dry_run": dry_run,
        "source": {
            "data_dir": str(data_dir.resolve()),
            "runtime_subdirs": ["audio", "decks", "corpus"],
            "runtime_counts": runtime_counts,
        },
        "destination": str(destination.resolve()),
        "database": database_meta,
        "database_counts": database_counts or {},
        "consistency_boundary": {
            "database_counts": "captured immediately before pg_dump",
            "database_dump": "transaction-consistent pg_dump snapshot",
            "runtime_copy": "begins only after pg_dump completes",
            "active_writes_note": (
                "For exact database-count comparison, quiesce API and worker writes "
                "during count capture and pg_dump."
            ),
        },
        "artifacts": {
            "postgres_dump": POSTGRES_DUMP_RELATIVE.as_posix(),
            "runtime_root": RUNTIME_ROOT_RELATIVE.as_posix(),
            "checksums": CHECKSUMS_FILENAME,
            "expected_file_count": expected_file_count,
        },
        "excluded": sorted(EXCLUDED_RELATIVE_NAMES),
    }
    logger.info(
        "build_manifest called backup_id=%s dry_run=%s runtime_counts=%s database_table_count=%s",
        backup_id,
        dry_run,
        runtime_counts,
        len(database_counts or {}),
    )
    return manifest


def write_manifest(destination: Path, manifest: dict[str, Any]) -> Path:
    """Write ``manifest.json`` under a backup destination.

    Args:
        destination: Backup root directory.
        manifest: Metadata-only manifest dictionary.

    Returns:
        Path to the written manifest file.

    Side effects:
        Creates parent directories as needed and writes JSON to disk.
    """
    path = destination / MANIFEST_FILENAME
    logger.info("write_manifest called destination=%s", destination)
    destination.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_checksums(destination: Path, lines: list[str]) -> Path:
    """Write aggregate checksum lines for a completed backup.

    Args:
        destination: Backup root directory.
        lines: Sorted GNU-style checksum lines.

    Returns:
        Path to ``checksums.sha256``.

    Side effects:
        Writes checksum metadata to disk.
    """
    path = destination / CHECKSUMS_FILENAME
    logger.info("write_checksums called destination=%s line_count=%s", destination, len(lines))
    destination.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def count_runtime_entries(data_dir: Path) -> dict[str, int]:
    """Count files under contract runtime directories for manifest metadata.

    Args:
        data_dir: Runtime-data root.

    Returns:
        Mapping of subtree name to file count, excluding secret files.

    Side effects:
        Walks runtime directories without logging individual filenames.
    """
    counts: dict[str, int] = {}
    for name in ("audio", "decks", "corpus"):
        root = data_dir / name
        if not root.exists():
            counts[name] = 0
            continue
        counts[name] = sum(
            1
            for path in root.rglob("*")
            if path.is_file() and should_include_in_backup(path.relative_to(root))
        )
    logger.info("count_runtime_entries called data_dir=%s counts=%s", data_dir, counts)
    return counts
