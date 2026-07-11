"""Deterministic Compose command construction for recovery operations.

The full production stack is expected to expose ``postgres``, ``api``, and
``worker`` Compose services with healthchecks. Pure builders keep status,
restart, database preflight, and integrity-query behavior testable without
executing service-control commands against a live stack.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_COMPOSE_FILE = "docker-compose.yml"
COMPOSE_SERVICES = ("postgres", "api", "worker")
CORE_TABLES = (
    "players",
    "pairs",
    "matchmaking_queue",
    "decks",
    "cards",
    "turns",
    "jobs",
    "records",
    "metrics_counters",
    "api_calls",
    "speaker_audio_fingerprints",
    "worker_heartbeats",
)


@dataclass(frozen=True, slots=True)
class CommandStep:
    """One operator-visible recovery step with a safe description and argv."""

    name: str
    argv: tuple[str, ...]
    description: str


def database_log_meta(database_url: str) -> dict[str, str | int | None]:
    """Return redacted Postgres metadata suitable for manifests and logs.

    Args:
        database_url: Full Postgres DSN that may contain credentials.

    Returns:
        Host, port, database, and scheme without user or password values.

    Side effects:
        Logs only non-secret metadata at INFO.
    """
    parsed = urlparse(database_url)
    meta = {
        "scheme": parsed.scheme,
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": (parsed.path or "").lstrip("/") or None,
    }
    logger.info("database_log_meta called database=%s host=%s", meta["database"], meta["host"])
    return meta


def build_pg_dump_command(
    *,
    compose_file: Path,
    compose_project: str,
    postgres_user: str,
    postgres_db: str,
    output_file: Path,
    use_compose: bool = True,
) -> CommandStep:
    """Build the DB-first Postgres dump command executed before runtime copies.

    Args:
        compose_file: Compose file path relative to the repository root.
        compose_project: Compose project name used for the Postgres service.
        postgres_user: Database role passed to ``pg_dump``.
        postgres_db: Database name passed to ``pg_dump``.
        output_file: Destination for the gzipped SQL dump.
        use_compose: When True, dump through ``docker compose exec``; otherwise
            assume a local ``pg_dump`` with port-forwarded Postgres.

    Returns:
        A ``CommandStep`` whose argv writes a gzipped dump to ``output_file``.

    Side effects:
        None.
    """
    logger.info(
        "build_pg_dump_command called compose_project=%s postgres_db=%s output_file=%s use_compose=%s",
        compose_project,
        postgres_db,
        output_file,
        use_compose,
    )
    if use_compose:
        argv = (
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "-p",
            compose_project,
            "exec",
            "-T",
            "postgres",
            "pg_dump",
            "-U",
            postgres_user,
            postgres_db,
        )
        description = (
            f"Dump Postgres database {postgres_db} via compose project {compose_project} "
            f"into {output_file}"
        )
    else:
        argv = ("pg_dump", "-U", postgres_user, postgres_db)
        description = f"Dump Postgres database {postgres_db} into {output_file}"
    return CommandStep(name="postgres_dump", argv=argv, description=description)


def build_runtime_copy_command(source_dir: Path, destination_dir: Path) -> CommandStep:
    """Build the runtime-data copy command for audio, decks, and corpus trees.

    Args:
        source_dir: Live ``DATA_DIR`` root.
        destination_dir: Backup ``runtime/`` directory.

    Returns:
        A ``CommandStep`` using ``rsync`` with trailing-slash semantics.

    Side effects:
        None.
    """
    logger.info(
        "build_runtime_copy_command called source_dir=%s destination_dir=%s",
        source_dir,
        destination_dir,
    )
    argv = (
        "rsync",
        "-a",
        "--exclude",
        ".env*",
        f"{source_dir.as_posix()}/",
        f"{destination_dir.as_posix()}/",
    )
    description = f"Copy runtime data from {source_dir} to {destination_dir} excluding secret files"
    return CommandStep(name="runtime_copy", argv=argv, description=description)


def _compose_prefix(compose_file: Path, compose_project: str) -> tuple[str, ...]:
    """Return the common Docker Compose argv prefix.

    Args:
        compose_file: Compose file path relative to the repository root.
        compose_project: Compose project name.

    Returns:
        Immutable argv prefix shared by all Compose commands.

    Side effects:
        None.
    """
    return ("docker", "compose", "-f", str(compose_file), "-p", compose_project)


def build_compose_health_command(
    *,
    compose_file: Path,
    compose_project: str,
    service: str,
) -> CommandStep:
    """Build a machine-readable Compose service health query.

    Args:
        compose_file: Compose file path relative to the repository root.
        compose_project: Compose project name.
        service: One of the required full-stack service names.

    Returns:
        A ``CommandStep`` whose JSON output includes Compose health state.

    Raises:
        ValueError: If ``service`` is outside the required production stack.

    Side effects:
        None.
    """
    if service not in COMPOSE_SERVICES:
        raise ValueError(f"unsupported compose service: {service}")
    argv = (*_compose_prefix(compose_file, compose_project), "ps", "--format", "json", service)
    return CommandStep(
        name=f"health_{service}",
        argv=argv,
        description=f"Require Compose service {service} to report healthy",
    )


def build_worker_status_command(
    *,
    compose_file: Path = Path(DEFAULT_COMPOSE_FILE),
    compose_project: str = "neva",
) -> CommandStep:
    """Build the worker heartbeat-backed Compose health query.

    Args:
        compose_file: Compose file containing the worker service.
        compose_project: Compose project whose worker health is queried.

    Returns:
        A machine-readable worker health query. The worker's Compose
        healthcheck is assumed to execute the orchestrator-owned heartbeat
        command, so a wedged process cannot pass based on PID presence alone.

    Side effects:
        None.
    """
    return build_compose_health_command(
        compose_file=compose_file,
        compose_project=compose_project,
        service="worker",
    )


def build_database_counts_sql() -> str:
    """Build a payload-free JSON count query for all core contract tables.

    Returns:
        SQL that emits one JSON object containing only table row counts.

    Side effects:
        None.
    """
    pairs = ", ".join(
        f"'{table}', (SELECT count(*) FROM {table})"
        for table in CORE_TABLES
    )
    return f"SELECT json_build_object({pairs});"


def build_database_counts_command(
    *,
    compose_file: Path,
    compose_project: str,
    postgres_user: str,
    postgres_db: str,
) -> CommandStep:
    """Build the Compose psql command that captures metadata-only row counts.

    Args:
        compose_file: Compose file containing Postgres.
        compose_project: Compose project name.
        postgres_user: Postgres role name.
        postgres_db: Database name.

    Returns:
        A ``CommandStep`` emitting one JSON count object and no row payloads.

    Side effects:
        None.
    """
    argv = (
        *_compose_prefix(compose_file, compose_project),
        "exec",
        "-T",
        "postgres",
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        postgres_user,
        "-d",
        postgres_db,
        "-Atc",
        build_database_counts_sql(),
    )
    return CommandStep(
        name="database_counts",
        argv=argv,
        description=f"Capture metadata-only core table counts from {postgres_db}",
    )


def build_empty_database_preflight_command(
    *,
    compose_file: Path,
    compose_project: str,
    postgres_user: str,
    postgres_db: str,
) -> CommandStep:
    """Build the fail-closed query proving a target DB has no user tables.

    Args:
        compose_file: Compose file containing Postgres.
        compose_project: Explicitly isolated Compose project.
        postgres_user: Postgres role name.
        postgres_db: Explicitly isolated database name.

    Returns:
        A ``CommandStep`` emitting the number of non-system tables.

    Side effects:
        None.
    """
    sql = build_empty_database_sql()
    argv = (
        *_compose_prefix(compose_file, compose_project),
        "exec",
        "-T",
        "postgres",
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        postgres_user,
        "-d",
        postgres_db,
        "-Atc",
        sql,
    )
    return CommandStep(
        name="assert_empty_database",
        argv=argv,
        description=f"Require isolated database {postgres_db} to contain zero user tables",
    )


def build_empty_database_sql() -> str:
    """Build SQL that counts all non-system tables in a target database.

    Returns:
        Scalar SQL used by the fresh-database preflight.

    Side effects:
        None.
    """
    return (
        "SELECT count(*) FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r','p') "
        "AND n.nspname NOT IN ('pg_catalog','information_schema') "
        "AND n.nspname !~ '^pg_toast';"
    )


def build_constraint_validation_command(
    *,
    compose_file: Path,
    compose_project: str,
    postgres_user: str,
    postgres_db: str,
) -> CommandStep:
    """Build the post-restore query counting unvalidated constraints.

    Args:
        compose_file: Compose file containing Postgres.
        compose_project: Explicitly isolated Compose project.
        postgres_user: Postgres role name.
        postgres_db: Restored isolated database name.

    Returns:
        A ``CommandStep`` emitting zero only when all constraints are validated.

    Side effects:
        None.
    """
    sql = build_constraint_validation_sql()
    argv = (
        *_compose_prefix(compose_file, compose_project),
        "exec",
        "-T",
        "postgres",
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        postgres_user,
        "-d",
        postgres_db,
        "-Atc",
        sql,
    )
    return CommandStep(
        name="validate_constraints",
        argv=argv,
        description=f"Require all restored constraints in {postgres_db} to be validated",
    )


def build_constraint_validation_sql() -> str:
    """Build SQL that counts unvalidated user constraints.

    Returns:
        Scalar SQL used by the post-restore integrity gate.

    Side effects:
        None.
    """
    return (
        "SELECT count(*) FROM pg_catalog.pg_constraint c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
        "AND NOT c.convalidated;"
    )


def build_restart_sequence(
    *,
    compose_file: Path,
    compose_project: str,
) -> tuple[CommandStep, ...]:
    """Build the deterministic restart sequence for worker, API, and Postgres.

    The sequence intentionally stops the worker before Postgres so in-flight
    jobs can drain, restarts Postgres through Compose, then brings API and
    worker processes back. Callers must pass ``dry_run=True`` at the Bash layer
    during tests so live services are never stopped.

    Args:
        compose_file: Compose file path relative to the repository root.
        compose_project: Compose project name for Postgres.
    Returns:
        Ordered Compose steps from worker stop through worker health wait.

    Side effects:
        None.
    """
    logger.info(
        "build_restart_sequence called compose_project=%s",
        compose_project,
    )
    prefix = _compose_prefix(compose_file, compose_project)
    steps = (
        CommandStep(
            name="stop_worker",
            argv=(*prefix, "stop", "worker"),
            description="Stop the worker Compose service first",
        ),
        CommandStep(
            name="stop_api",
            argv=(*prefix, "stop", "api"),
            description="Stop the API Compose service second",
        ),
        CommandStep(
            name="restart_postgres",
            argv=(*prefix, "restart", "postgres"),
            description="Restart the Postgres compose service",
        ),
        CommandStep(
            name="wait_postgres",
            argv=build_compose_health_command(
                compose_file=compose_file,
                compose_project=compose_project,
                service="postgres",
            ).argv,
            description="Wait until Compose reports Postgres healthy",
        ),
        CommandStep(
            name="start_api",
            argv=(*prefix, "up", "-d", "--no-deps", "api"),
            description="Start the API Compose service",
        ),
        CommandStep(
            name="wait_api",
            argv=build_compose_health_command(
                compose_file=compose_file,
                compose_project=compose_project,
                service="api",
            ).argv,
            description="Wait until Compose reports API healthy",
        ),
        CommandStep(
            name="start_worker",
            argv=(*prefix, "up", "-d", "--no-deps", "worker"),
            description="Start the worker Compose service",
        ),
        CommandStep(
            name="wait_worker",
            argv=build_worker_status_command(
                compose_file=compose_file,
                compose_project=compose_project,
            ).argv,
            description="Wait for the worker heartbeat healthcheck to become healthy",
        ),
    )
    return steps
