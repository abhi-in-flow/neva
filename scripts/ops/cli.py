"""CLI helpers invoked by ``scripts/ops/neva-ops.sh`` for recovery operations.

Exposes validation, dry-run planning, and manifest finalization subcommands so
pytest can cover safety rules while Bash orchestrates external tools.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from scripts.ops.commands import (
    build_constraint_validation_command,
    build_constraint_validation_sql,
    build_database_counts_command,
    build_database_counts_sql,
    build_empty_database_preflight_command,
    build_empty_database_sql,
    build_pg_dump_command,
    build_restart_sequence,
    build_runtime_copy_command,
    database_log_meta,
)
from scripts.ops.health import ComposeHealthError, require_service_healthy
from scripts.ops.integrity import (
    BackupIntegrityError,
    build_restore_report,
    load_manifest,
    validate_post_restore_database,
    verify_backup_source,
    verify_restored_runtime,
    write_restore_report,
)
from scripts.ops.manifest import (
    POSTGRES_DUMP_RELATIVE,
    RUNTIME_ROOT_RELATIVE,
    build_manifest,
    collect_checksum_lines,
    count_runtime_entries,
    utc_timestamp,
    write_checksums,
    write_manifest,
)
from scripts.ops.paths import (
    OpsPathError,
    is_path_inside,
    resolve_path,
    validate_backup_source,
    validate_backup_destination,
    validate_restore_destination_empty,
    validate_restore_target,
)

logger = logging.getLogger(__name__)


def _parse_count_json(raw: str, *, field_name: str) -> dict[str, int]:
    """Parse a JSON object containing non-negative integer counts only.

    Args:
        raw: JSON text from a metadata-only psql count query.
        field_name: Operator-facing name used in validation errors.

    Returns:
        Count mapping with string keys and integer values.

    Raises:
        ValueError: If JSON shape or count values are invalid.

    Side effects:
        None.
    """
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    if not all(
        isinstance(key, str) and isinstance(value, int) and value >= 0
        for key, value in parsed.items()
    ):
        raise ValueError(f"{field_name} must contain non-negative integer counts")
    return parsed


def _configure_logging() -> None:
    """Configure INFO logging for CLI invocations.

    Side effects:
        Installs a basic logging configuration on the root logger.
    """
    logging.basicConfig(level=logging.INFO, format="INFO %(name)s: %(message)s")


def _cmd_validate_backup(args: argparse.Namespace) -> int:
    """Validate a backup destination and optionally emit a dry-run plan.

    Args:
        args: Parsed CLI namespace with destination and data directory paths.

    Returns:
        Process exit code ``0`` on success, ``2`` on validation failure.
    """
    destination = resolve_path(args.destination)
    data_dir = resolve_path(args.data_dir)
    try:
        validate_backup_source(data_dir)
        validate_backup_destination(destination, data_dir, exists=destination.exists())
    except OpsPathError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    backup_id = args.backup_id or utc_timestamp()
    runtime_counts = count_runtime_entries(data_dir)
    manifest = build_manifest(
        backup_id=backup_id,
        data_dir=data_dir,
        destination=destination,
        database_meta=database_log_meta(args.database_url),
        dry_run=args.dry_run,
        runtime_counts=runtime_counts,
    )
    dump_step = build_pg_dump_command(
        compose_file=Path(args.compose_file),
        compose_project=args.compose_project,
        postgres_user=args.postgres_user,
        postgres_db=args.postgres_db,
        output_file=destination / POSTGRES_DUMP_RELATIVE,
        use_compose=args.use_compose,
    )
    runtime_step = build_runtime_copy_command(
        data_dir,
        destination / RUNTIME_ROOT_RELATIVE,
    )
    payload = {
        "ok": True,
        "backup_id": backup_id,
        "manifest": manifest,
        "steps": [
            {"name": dump_step.name, "argv": list(dump_step.argv), "description": dump_step.description},
            {
                "name": runtime_step.name,
                "argv": list(runtime_step.argv),
                "description": runtime_step.description,
            },
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _cmd_finalize_backup(args: argparse.Namespace) -> int:
    """Write manifest and checksum metadata for a completed backup directory.

    Args:
        args: Parsed CLI namespace with destination and source metadata.

    Returns:
        Process exit code ``0`` on success.
    """
    destination = resolve_path(args.destination)
    data_dir = resolve_path(args.data_dir)
    runtime_root = destination / RUNTIME_ROOT_RELATIVE
    runtime_counts = count_runtime_entries(runtime_root)
    try:
        database_counts = _parse_count_json(
            args.database_counts_json,
            field_name="database counts",
        )
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    checksum_lines = collect_checksum_lines(
        destination / POSTGRES_DUMP_RELATIVE.parent,
        prefix=POSTGRES_DUMP_RELATIVE.parent,
    )
    checksum_lines.extend(
        collect_checksum_lines(
            runtime_root,
            prefix=RUNTIME_ROOT_RELATIVE,
        ),
    )
    manifest = build_manifest(
        backup_id=args.backup_id,
        data_dir=data_dir,
        destination=destination,
        database_meta=database_log_meta(args.database_url),
        dry_run=False,
        runtime_counts=runtime_counts,
        database_counts=database_counts,
        expected_file_count=len(checksum_lines),
    )
    write_manifest(destination, manifest)
    write_checksums(destination, sorted(checksum_lines))
    print(json.dumps({"ok": True, "manifest": str(destination / "manifest.json")}))
    return 0


def _cmd_validate_restore(args: argparse.Namespace) -> int:
    """Validate an isolated restore target and refuse overwrite.

    Args:
        args: Parsed CLI namespace describing restore source and target paths.

    Returns:
        Process exit code ``0`` when validation succeeds, ``2`` otherwise.
    """
    source = resolve_path(args.source)
    data_dir = resolve_path(args.data_dir)
    try:
        validate_restore_target(
            data_dir=data_dir,
            database_url=args.database_url,
            compose_project=args.compose_project,
            isolated_env=args.isolated_env,
            live_data_dir=resolve_path(args.live_data_dir) if args.live_data_dir else None,
            live_database_url=args.live_database_url,
        )
        if args.require_empty:
            validate_restore_destination_empty(data_dir)
        source_summary = verify_backup_source(source)
    except (OpsPathError, BackupIntegrityError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    empty_step = build_empty_database_preflight_command(
        compose_file=Path(args.compose_file),
        compose_project=args.compose_project,
        postgres_user=args.postgres_user,
        postgres_db=args.postgres_db,
    )
    counts_step = build_database_counts_command(
        compose_file=Path(args.compose_file),
        compose_project=args.compose_project,
        postgres_user=args.postgres_user,
        postgres_db=args.postgres_db,
    )
    constraints_step = build_constraint_validation_command(
        compose_file=Path(args.compose_file),
        compose_project=args.compose_project,
        postgres_user=args.postgres_user,
        postgres_db=args.postgres_db,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "source": str(source),
                "data_dir": str(data_dir),
                "source_integrity": source_summary,
                "steps": [
                    {
                        "name": step.name,
                        "argv": list(step.argv),
                        "description": step.description,
                    }
                    for step in (empty_step, counts_step, constraints_step)
                ],
            },
        ),
    )
    return 0


def _cmd_validate_db_empty(args: argparse.Namespace) -> int:
    """Fail closed unless the isolated target DB contains zero user tables.

    Args:
        args: Parsed CLI namespace containing the psql scalar result.

    Returns:
        Process exit code ``0`` only for exactly zero user tables.
    """
    logger.info("_cmd_validate_db_empty called")
    try:
        table_count = int(args.user_table_count.strip())
    except ValueError:
        print(json.dumps({"ok": False, "error": "database preflight result is not an integer"}))
        return 2
    if table_count != 0:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "isolated target database is not fresh; user tables already exist",
                    "user_table_count": table_count,
                },
            ),
        )
        return 2
    print(json.dumps({"ok": True, "user_table_count": 0}))
    return 0


def _cmd_verify_restore(args: argparse.Namespace) -> int:
    """Verify runtime and DB integrity and write a metadata-only RTO report.

    Args:
        args: Parsed CLI namespace containing source, target, count, constraint,
            elapsed-time, and report metadata.

    Returns:
        Process exit code ``0`` on full post-restore integrity, ``2`` otherwise.
    """
    source = resolve_path(args.source)
    data_dir = resolve_path(args.data_dir)
    report_path = resolve_path(args.report)
    try:
        if is_path_inside(report_path, data_dir):
            raise BackupIntegrityError("restore report must remain outside target DATA_DIR")
        if is_path_inside(report_path, source / POSTGRES_DUMP_RELATIVE.parent) or is_path_inside(
            report_path,
            source / RUNTIME_ROOT_RELATIVE,
        ):
            raise BackupIntegrityError("restore report must remain outside protected backup artifacts")
        verify_backup_source(source)
        manifest = load_manifest(source)
        actual_database_counts = _parse_count_json(
            args.actual_database_counts_json,
            field_name="actual database counts",
        )
        invalid_constraint_count = int(args.invalid_constraint_count.strip())
        elapsed_seconds = float(args.elapsed_seconds)
        runtime_counts = verify_restored_runtime(
            source=source,
            target_data_dir=data_dir,
            manifest=manifest,
        )
        validate_post_restore_database(
            expected_counts=manifest["database_counts"],
            actual_counts=actual_database_counts,
            invalid_constraint_count=invalid_constraint_count,
        )
        report = build_restore_report(
            manifest=manifest,
            runtime_counts=runtime_counts,
            database_counts=actual_database_counts,
            invalid_constraint_count=invalid_constraint_count,
            elapsed_seconds=elapsed_seconds,
        )
        write_restore_report(report_path, report)
    except (
        BackupIntegrityError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": True, "report": str(report_path), "elapsed_seconds": elapsed_seconds}))
    return 0


def _cmd_validate_compose_health(args: argparse.Namespace) -> int:
    """Validate JSON status for one Compose service.

    Args:
        args: Parsed CLI namespace containing service name and JSON text.

    Returns:
        Process exit code ``0`` when running and healthy, ``2`` otherwise.
    """
    try:
        summary = require_service_healthy(args.json, args.service)
    except ComposeHealthError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": True, **summary}))
    return 0


def _cmd_emit_sql(args: argparse.Namespace) -> int:
    """Print one centralized metadata-only operational SQL statement.

    Args:
        args: Parsed CLI namespace selecting counts, empty-DB, or constraints.

    Returns:
        Process exit code ``0``.
    """
    builders = {
        "database-counts": build_database_counts_sql,
        "empty-database": build_empty_database_sql,
        "constraints": build_constraint_validation_sql,
    }
    logger.info("_cmd_emit_sql called kind=%s", args.kind)
    print(builders[args.kind]())
    return 0


def _cmd_restart_plan(args: argparse.Namespace) -> int:
    """Emit the deterministic restart sequence as JSON for Bash dry-run mode.

    Args:
        args: Parsed CLI namespace with compose metadata.

    Returns:
        Process exit code ``0``.
    """
    steps = build_restart_sequence(
        compose_file=Path(args.compose_file),
        compose_project=args.compose_project,
    )
    payload = {
        "ok": True,
        "steps": [
            {"name": step.name, "argv": list(step.argv), "description": step.description}
            for step in steps
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the recovery CLI parser shared by tests and Bash callers.

    Returns:
        Configured ``ArgumentParser`` with validation and planning subcommands.
    """
    parser = argparse.ArgumentParser(description="Dialect Data Factory recovery helpers")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_backup = subparsers.add_parser("validate-backup")
    validate_backup.add_argument("--destination", required=True)
    validate_backup.add_argument("--data-dir", required=True)
    validate_backup.add_argument("--database-url", required=True)
    validate_backup.add_argument("--compose-file", default="docker-compose.yml")
    validate_backup.add_argument("--compose-project", default="neva")
    validate_backup.add_argument("--postgres-user", default="dialect")
    validate_backup.add_argument("--postgres-db", default="dialect_factory")
    validate_backup.add_argument("--backup-id")
    validate_backup.add_argument("--dry-run", action="store_true")
    validate_backup.add_argument("--use-compose", action="store_true", default=True)
    validate_backup.set_defaults(func=_cmd_validate_backup)

    finalize_backup = subparsers.add_parser("finalize-backup")
    finalize_backup.add_argument("--destination", required=True)
    finalize_backup.add_argument("--data-dir", required=True)
    finalize_backup.add_argument("--database-url", required=True)
    finalize_backup.add_argument("--backup-id", required=True)
    finalize_backup.add_argument("--database-counts-json", required=True)
    finalize_backup.set_defaults(func=_cmd_finalize_backup)

    validate_restore = subparsers.add_parser("validate-restore")
    validate_restore.add_argument("--source", required=True)
    validate_restore.add_argument("--data-dir", required=True)
    validate_restore.add_argument("--database-url", required=True)
    validate_restore.add_argument("--compose-project", required=True)
    validate_restore.add_argument("--compose-file", default="docker-compose.yml")
    validate_restore.add_argument("--postgres-user", default="dialect")
    validate_restore.add_argument("--postgres-db", required=True)
    validate_restore.add_argument("--isolated-env")
    validate_restore.add_argument("--live-data-dir")
    validate_restore.add_argument("--live-database-url")
    validate_restore.add_argument("--require-empty", action="store_true", default=True)
    validate_restore.set_defaults(func=_cmd_validate_restore)

    validate_db_empty = subparsers.add_parser("validate-db-empty")
    validate_db_empty.add_argument("--user-table-count", required=True)
    validate_db_empty.set_defaults(func=_cmd_validate_db_empty)

    verify_restore = subparsers.add_parser("verify-restore")
    verify_restore.add_argument("--source", required=True)
    verify_restore.add_argument("--data-dir", required=True)
    verify_restore.add_argument("--actual-database-counts-json", required=True)
    verify_restore.add_argument("--invalid-constraint-count", required=True)
    verify_restore.add_argument("--elapsed-seconds", required=True)
    verify_restore.add_argument("--report", required=True)
    verify_restore.set_defaults(func=_cmd_verify_restore)

    compose_health = subparsers.add_parser("validate-compose-health")
    compose_health.add_argument("--service", choices=("postgres", "api", "worker"), required=True)
    compose_health.add_argument("--json", required=True)
    compose_health.set_defaults(func=_cmd_validate_compose_health)

    emit_sql = subparsers.add_parser("emit-sql")
    emit_sql.add_argument(
        "--kind",
        choices=("database-counts", "empty-database", "constraints"),
        required=True,
    )
    emit_sql.set_defaults(func=_cmd_emit_sql)

    restart_plan = subparsers.add_parser("restart-plan")
    restart_plan.add_argument("--compose-file", default="docker-compose.yml")
    restart_plan.add_argument("--compose-project", default="neva")
    restart_plan.set_defaults(func=_cmd_restart_plan)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a recovery helper subcommand.

    Args:
        argv: Optional argument vector; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code from the selected subcommand handler.
    """
    _configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    logger.info("main called command=%s", args.command)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
