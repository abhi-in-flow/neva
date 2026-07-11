"""Fail-closed backup and post-restore integrity verification.

This module validates metadata-only manifests, exact checksum coverage, gzip
readability, secret exclusion, restored runtime files, database count equality,
and constraint status. It never reads or logs participant payloads except to
stream bytes through SHA-256 or gzip integrity checks.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.ops.commands import CORE_TABLES
from scripts.ops.manifest import (
    CHECKSUMS_FILENAME,
    MANIFEST_FILENAME,
    MANIFEST_VERSION,
    POSTGRES_DUMP_RELATIVE,
    RUNTIME_ROOT_RELATIVE,
    count_runtime_entries,
    sha256_file,
    should_include_in_backup,
)
from scripts.ops.paths import ISOLATED_MARKER_FILENAME, is_secret_filename

logger = logging.getLogger(__name__)

CHECKSUM_LINE = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<path>[^\r\n]+)$")
REQUIRED_MANIFEST_KEYS = frozenset(
    {
        "manifest_version",
        "backup_id",
        "source",
        "database",
        "database_counts",
        "artifacts",
        "excluded",
    },
)


class BackupIntegrityError(ValueError):
    """Raised when a backup or restored target fails an integrity gate."""


def _is_secret_path(path: Path) -> bool:
    """Return True when any path segment is a forbidden secret filename.

    Args:
        path: Relative or absolute artifact path.

    Returns:
        True for ``.env`` and configured environment-file variants.

    Side effects:
        None.
    """
    return any(is_secret_filename(part) for part in path.parts)


def load_manifest(source: Path) -> dict[str, Any]:
    """Load and structurally validate a metadata-only backup manifest.

    Args:
        source: Backup root containing ``manifest.json``.

    Returns:
        Parsed manifest dictionary.

    Raises:
        BackupIntegrityError: If the file is absent, malformed, unsupported, or
            missing required metadata.

    Side effects:
        Reads only the manifest JSON and logs its backup identifier.
    """
    manifest_path = source / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise BackupIntegrityError("backup source missing manifest.json")
    try:
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupIntegrityError("backup manifest is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise BackupIntegrityError("backup manifest must be a JSON object")
    missing = REQUIRED_MANIFEST_KEYS - parsed.keys()
    if missing:
        raise BackupIntegrityError(f"backup manifest missing keys: {sorted(missing)}")
    if parsed.get("manifest_version") != MANIFEST_VERSION:
        raise BackupIntegrityError("backup manifest version is unsupported")
    if parsed.get("dry_run") is True:
        raise BackupIntegrityError("dry-run manifest cannot be restored")
    database_counts = parsed.get("database_counts")
    if not isinstance(database_counts, dict) or not database_counts:
        raise BackupIntegrityError("backup manifest database counts are missing")
    if not all(
        isinstance(key, str) and isinstance(value, int) and value >= 0
        for key, value in database_counts.items()
    ):
        raise BackupIntegrityError("backup manifest database counts are invalid")
    if set(database_counts) != set(CORE_TABLES):
        raise BackupIntegrityError("backup manifest core-table counts are incomplete")
    artifacts = parsed.get("artifacts")
    if not isinstance(artifacts, dict):
        raise BackupIntegrityError("backup manifest artifacts metadata is invalid")
    if artifacts.get("postgres_dump") != POSTGRES_DUMP_RELATIVE.as_posix():
        raise BackupIntegrityError("backup manifest Postgres dump path is invalid")
    if artifacts.get("runtime_root") != RUNTIME_ROOT_RELATIVE.as_posix():
        raise BackupIntegrityError("backup manifest runtime root is invalid")
    logger.info("load_manifest called backup_id=%s", parsed.get("backup_id"))
    return parsed


def parse_checksums(source: Path) -> dict[Path, str]:
    """Parse and validate the aggregate checksum index.

    Args:
        source: Backup root containing ``checksums.sha256``.

    Returns:
        Mapping of safe relative artifact paths to expected SHA-256 digests.

    Raises:
        BackupIntegrityError: For malformed, duplicate, absolute, traversing, or
            secret paths.

    Side effects:
        Reads checksum metadata only.
    """
    checksum_path = source / CHECKSUMS_FILENAME
    if not checksum_path.is_file():
        raise BackupIntegrityError("backup source missing checksums.sha256")
    try:
        checksum_lines = checksum_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise BackupIntegrityError("checksum index is not valid UTF-8 text") from exc
    entries: dict[Path, str] = {}
    for line_number, line in enumerate(checksum_lines, start=1):
        match = CHECKSUM_LINE.fullmatch(line)
        if match is None:
            raise BackupIntegrityError(f"malformed checksum line {line_number}")
        relative = Path(match.group("path"))
        if relative.is_absolute() or ".." in relative.parts:
            raise BackupIntegrityError("checksum path must remain inside backup source")
        if _is_secret_path(relative):
            raise BackupIntegrityError("secret file appears in checksum index")
        if relative in entries:
            raise BackupIntegrityError("duplicate path in checksum index")
        entries[relative] = match.group("digest")
    if not entries:
        raise BackupIntegrityError("checksum index is empty")
    logger.info("parse_checksums called source=%s entry_count=%s", source, len(entries))
    return entries


def expected_backup_files(source: Path) -> set[Path]:
    """Return the exact dump/runtime files that checksums must protect.

    Args:
        source: Backup root directory.

    Returns:
        Relative paths under ``postgres/`` and ``runtime/``.

    Raises:
        BackupIntegrityError: If any secret file is present in protected trees.

    Side effects:
        Walks artifact directories without reading payload contents.
    """
    expected: set[Path] = set()
    for root_name in (POSTGRES_DUMP_RELATIVE.parent, RUNTIME_ROOT_RELATIVE):
        root = source / root_name
        if not root.is_dir():
            raise BackupIntegrityError(f"backup source missing {root_name.as_posix()}/")
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            relative = file_path.relative_to(source)
            if _is_secret_path(relative) or not should_include_in_backup(relative):
                raise BackupIntegrityError("secret file appears in backup artifacts")
            expected.add(relative)
    return expected


def verify_gzip_stream(path: Path) -> int:
    """Read a gzip stream fully to validate its framing and trailer.

    Args:
        path: Gzipped Postgres dump.

    Returns:
        Number of uncompressed bytes streamed.

    Raises:
        BackupIntegrityError: If the stream is missing, corrupt, or empty.

    Side effects:
        Reads and decompresses the dump without persisting SQL or logging it.
    """
    if not path.is_file():
        raise BackupIntegrityError("backup source missing Postgres dump")
    byte_count = 0
    try:
        with gzip.open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                byte_count += len(chunk)
    except (OSError, EOFError) as exc:
        raise BackupIntegrityError("Postgres dump gzip stream is corrupt") from exc
    if byte_count == 0:
        raise BackupIntegrityError("Postgres dump gzip stream is empty")
    logger.info("verify_gzip_stream called path=%s uncompressed_bytes=%s", path, byte_count)
    return byte_count


def verify_backup_source(source: Path) -> dict[str, Any]:
    """Verify manifest, exact checksums, gzip validity, counts, and exclusions.

    Args:
        source: Backup root to validate before any restore action.

    Returns:
        Metadata-only verification summary.

    Raises:
        BackupIntegrityError: On any missing, corrupt, unexpected, or secret
            artifact.

    Side effects:
        Reads metadata and streams protected files for hashing/decompression.
    """
    for file_path in source.rglob("*"):
        if file_path.is_symlink():
            raise BackupIntegrityError("symbolic links are not allowed in backup source")
        if file_path.is_file() and _is_secret_path(file_path.relative_to(source)):
            raise BackupIntegrityError("secret file appears in backup source")
    manifest = load_manifest(source)
    checksums = parse_checksums(source)
    expected = expected_backup_files(source)
    if set(checksums) != expected:
        missing = sorted(path.as_posix() for path in expected - set(checksums))
        unexpected = sorted(path.as_posix() for path in set(checksums) - expected)
        raise BackupIntegrityError(
            f"checksum coverage mismatch missing={missing} unexpected={unexpected}",
        )
    manifest_expected = manifest["artifacts"].get("expected_file_count")
    if manifest_expected != len(expected):
        raise BackupIntegrityError("manifest expected file count does not match backup artifacts")
    for relative, expected_digest in checksums.items():
        if sha256_file(source / relative) != expected_digest:
            raise BackupIntegrityError(f"checksum mismatch for {relative.as_posix()}")
    gzip_bytes = verify_gzip_stream(source / POSTGRES_DUMP_RELATIVE)
    runtime_counts = count_runtime_entries(source / RUNTIME_ROOT_RELATIVE)
    if runtime_counts != manifest["source"].get("runtime_counts"):
        raise BackupIntegrityError("runtime file counts do not match manifest")
    summary = {
        "backup_id": manifest["backup_id"],
        "checked_file_count": len(expected),
        "runtime_counts": runtime_counts,
        "database_counts": manifest["database_counts"],
        "gzip_uncompressed_bytes": gzip_bytes,
    }
    logger.info(
        "verify_backup_source completed backup_id=%s checked_file_count=%s",
        manifest["backup_id"],
        len(expected),
    )
    return summary


def verify_restored_runtime(
    *,
    source: Path,
    target_data_dir: Path,
    manifest: dict[str, Any],
) -> dict[str, int]:
    """Compare restored runtime files to protected backup checksums and counts.

    Args:
        source: Verified backup root.
        target_data_dir: Isolated restored ``DATA_DIR``.
        manifest: Parsed backup manifest.

    Returns:
        Actual runtime file counts after restore.

    Raises:
        BackupIntegrityError: On missing, additional, changed, or secret files.

    Side effects:
        Walks and hashes restored runtime files; preserves and ignores only the
        root ``.neva-isolated`` control marker.
    """
    checksums = parse_checksums(source)
    runtime_checksums = {
        path.relative_to(RUNTIME_ROOT_RELATIVE): digest
        for path, digest in checksums.items()
        if path.parts and path.parts[0] == RUNTIME_ROOT_RELATIVE.name
    }
    actual_files: set[Path] = set()
    for file_path in target_data_dir.rglob("*"):
        if file_path.is_symlink():
            raise BackupIntegrityError("symbolic links are not allowed in restored runtime data")
        if not file_path.is_file():
            continue
        relative = file_path.relative_to(target_data_dir)
        if relative == Path(ISOLATED_MARKER_FILENAME):
            continue
        if _is_secret_path(relative):
            raise BackupIntegrityError("secret file appears in restored runtime data")
        actual_files.add(relative)
    if actual_files != set(runtime_checksums):
        raise BackupIntegrityError("restored runtime file set differs from backup")
    for relative, expected_digest in runtime_checksums.items():
        if sha256_file(target_data_dir / relative) != expected_digest:
            raise BackupIntegrityError(f"restored checksum mismatch for {relative.as_posix()}")
    actual_counts = count_runtime_entries(target_data_dir)
    if actual_counts != manifest["source"].get("runtime_counts"):
        raise BackupIntegrityError("restored runtime counts do not match manifest")
    if not (target_data_dir / ISOLATED_MARKER_FILENAME).is_file():
        raise BackupIntegrityError("isolated marker did not survive runtime restore")
    logger.info(
        "verify_restored_runtime completed data_dir=%s counts=%s",
        target_data_dir,
        actual_counts,
    )
    return actual_counts


def validate_post_restore_database(
    *,
    expected_counts: dict[str, int],
    actual_counts: dict[str, int],
    invalid_constraint_count: int,
) -> None:
    """Require restored table counts and constraint state to match expectations.

    Args:
        expected_counts: Manifest row counts captured before backup.
        actual_counts: Metadata-only row counts queried after restore.
        invalid_constraint_count: Number of unvalidated user constraints.

    Raises:
        BackupIntegrityError: If counts differ or constraints are unvalidated.

    Side effects:
        None.
    """
    if actual_counts != expected_counts:
        raise BackupIntegrityError("restored database counts do not match manifest")
    if invalid_constraint_count != 0:
        raise BackupIntegrityError("restored database contains unvalidated constraints")


def build_restore_report(
    *,
    manifest: dict[str, Any],
    runtime_counts: dict[str, int],
    database_counts: dict[str, int],
    invalid_constraint_count: int,
    elapsed_seconds: float,
) -> dict[str, Any]:
    """Build a metadata-only post-restore verification report.

    Args:
        manifest: Verified source backup manifest.
        runtime_counts: Restored aggregate file counts.
        database_counts: Restored aggregate core-table row counts.
        invalid_constraint_count: Number of unvalidated constraints.
        elapsed_seconds: End-to-end restore verification RTO measurement.

    Returns:
        JSON-serializable report containing no row or participant payloads.

    Side effects:
        None.
    """
    return {
        "report_version": 1,
        "backup_id": manifest["backup_id"],
        "verified_at": datetime.now(UTC).isoformat(),
        "status": "pass",
        "elapsed_seconds": round(elapsed_seconds, 3),
        "runtime_counts": runtime_counts,
        "database_counts": database_counts,
        "invalid_constraint_count": invalid_constraint_count,
        "checks": {
            "source_manifest": "pass",
            "source_checksums": "pass",
            "source_gzip": "pass",
            "runtime_checksums": "pass",
            "runtime_counts": "pass",
            "database_counts": "pass",
            "constraints": "pass",
            "isolated_marker_preserved": "pass",
        },
    }


def write_restore_report(path: Path, report: dict[str, Any]) -> None:
    """Write a restore report without overwriting an existing report.

    Args:
        path: New report destination outside runtime payload directories.
        report: Metadata-only report from ``build_restore_report``.

    Raises:
        BackupIntegrityError: If ``path`` already exists.

    Side effects:
        Creates parent directories and writes JSON with mode inherited from the
        operator's umask.
    """
    if path.exists():
        raise BackupIntegrityError("restore verification report already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    logger.info("write_restore_report called path=%s", path)
