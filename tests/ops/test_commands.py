"""Command construction tests for Wave 2 recovery operations."""

from __future__ import annotations

import logging
from pathlib import Path

from scripts.ops.commands import (
    build_constraint_validation_command,
    build_database_counts_command,
    build_empty_database_preflight_command,
    build_pg_dump_command,
    build_restart_sequence,
    build_runtime_copy_command,
    build_worker_status_command,
    database_log_meta,
)

LOGGER = logging.getLogger(__name__)


def test_database_log_meta_redacts_credentials() -> None:
    """Return host and database metadata without credentials."""
    LOGGER.info("test_database_log_meta_redacts_credentials called")
    meta = database_log_meta("postgresql://dialect:secret@localhost:5432/dialect_factory")
    assert meta["host"] == "localhost"
    assert meta["database"] == "dialect_factory"
    assert "secret" not in str(meta)
    LOGGER.info("test_database_log_meta_redacts_credentials completed")


def test_pg_dump_command_uses_compose_exec() -> None:
    """Build a compose-backed pg_dump command for DB-first backup ordering."""
    LOGGER.info("test_pg_dump_command_uses_compose_exec called")
    step = build_pg_dump_command(
        compose_file=Path("docker-compose.yml"),
        compose_project="neva",
        postgres_user="dialect",
        postgres_db="dialect_factory",
        output_file=Path("/tmp/backup/postgres/dump.sql.gz"),
    )
    assert step.argv[0:3] == ("docker", "compose", "-f")
    assert "exec" in step.argv
    assert "-U" in step.argv
    assert "dialect" in step.argv
    assert step.argv[-1] == "dialect_factory"
    LOGGER.info("test_pg_dump_command_uses_compose_exec completed")


def test_runtime_copy_command_excludes_env(runtime_tree: Path, tmp_path: Path) -> None:
    """Build rsync command that excludes secret env files."""
    LOGGER.info("test_runtime_copy_command_excludes_env called")
    step = build_runtime_copy_command(runtime_tree, tmp_path / "runtime")
    assert "--exclude" in step.argv
    assert ".env*" in step.argv
    assert step.name == "runtime_copy"
    LOGGER.info("test_runtime_copy_command_excludes_env completed")


def test_restart_sequence_order_is_deterministic() -> None:
    """Emit worker stop, API stop, Postgres restart, then service starts."""
    LOGGER.info("test_restart_sequence_order_is_deterministic called")
    steps = build_restart_sequence(
        compose_file=Path("docker-compose.yml"),
        compose_project="neva",
    )
    names = [step.name for step in steps]
    assert names == [
        "stop_worker",
        "stop_api",
        "restart_postgres",
        "wait_postgres",
        "start_api",
        "wait_api",
        "start_worker",
        "wait_worker",
    ]
    assert all("docker" == step.argv[0] for step in steps)
    assert steps[0].argv[-2:] == ("stop", "worker")
    assert steps[1].argv[-2:] == ("stop", "api")
    LOGGER.info("test_restart_sequence_order_is_deterministic completed")


def test_worker_status_uses_compose_heartbeat_health() -> None:
    """Build a worker health query without process-presence checks."""
    LOGGER.info("test_health_and_worker_status_commands called")
    worker_step = build_worker_status_command(
        compose_file=Path("docker-compose.yml"),
        compose_project="neva",
    )
    assert worker_step.argv[-4:] == ("ps", "--format", "json", "worker")
    assert "pgrep" not in worker_step.argv
    LOGGER.info("test_health_and_worker_status_commands completed")


def test_restore_database_preflight_and_verification_commands() -> None:
    """Build payload-free preflight, row-count, and constraint queries."""
    LOGGER.info("test_restore_database_preflight_and_verification_commands called")
    kwargs = {
        "compose_file": Path("docker-compose.yml"),
        "compose_project": "neva_isolated",
        "postgres_user": "dialect",
        "postgres_db": "dialect_factory_isolated",
    }
    empty = build_empty_database_preflight_command(**kwargs)
    counts = build_database_counts_command(**kwargs)
    constraints = build_constraint_validation_command(**kwargs)
    assert empty.name == "assert_empty_database"
    assert "pg_catalog.pg_class" in empty.argv[-1]
    assert counts.name == "database_counts"
    assert "json_build_object" in counts.argv[-1]
    assert "worker_heartbeats" in counts.argv[-1]
    assert constraints.name == "validate_constraints"
    assert "convalidated" in constraints.argv[-1]
    assert all(step.argv[-2] == "-Atc" for step in (empty, counts, constraints))
    LOGGER.info("test_restore_database_preflight_and_verification_commands completed")
