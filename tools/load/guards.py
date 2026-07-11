"""Fail-closed safety guards for isolated load testing.

Validates loopback/private targets, explicit isolated markers, load-specific
database metadata, and DATA_DIR isolation before any HTTP, database, or
filesystem traffic is attempted.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

from tools.load.config import (
    MAX_CONCURRENT_REQUEST_WORKERS,
    REQUIRED_ENVIRONMENT,
    REQUIRED_MARKER_VALUE,
    LoadConfig,
)

LOGGER = logging.getLogger(__name__)

LOAD_DB_NAME_PATTERN = re.compile(r"(load|loadtest|load_test)", re.IGNORECASE)
PRIVATE_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}


class GuardViolation(Exception):
    """Raised when a load run fails a safety precondition."""

    def __init__(self, message: str, code: str) -> None:
        """Store a human-readable message and stable machine code.

        Args:
            message: Operator-facing explanation.
            code: Short identifier for tests and logs.
        """
        super().__init__(message)
        self.code = code
        self.message = message


def is_private_or_loopback_host(host: str | None) -> bool:
    """Return whether a hostname or IP is loopback or RFC1918 private.

    Args:
        host: Parsed hostname from the target URL.

    Returns:
        ``True`` when the host is considered safe for isolated load traffic.
    """
    LOGGER.info("is_private_or_loopback_host called host=%s", host)
    if not host:
        return False
    normalized = host.strip("[]").lower()
    if normalized in PRIVATE_HOSTNAMES:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def is_repo_data_dir(data_dir: Path, repo_root: Path) -> bool:
    """Return whether ``data_dir`` resolves to the repository runtime data tree.

    Args:
        data_dir: Candidate isolated data directory.
        repo_root: Repository root used to detect the default ``data/`` path.

    Returns:
        ``True`` when the path equals or nests under ``<repo>/data``.
    """
    LOGGER.info(
        "is_repo_data_dir called data_dir=%s repo_root=%s",
        data_dir,
        repo_root,
    )
    repo_data = (repo_root / "data").resolve()
    candidate = data_dir.resolve()
    return candidate == repo_data or repo_data in candidate.parents


def validate_isolated_marker(marker: str) -> None:
    """Require the explicit non-production isolated marker value.

    Args:
        marker: Operator-supplied marker string.

    Raises:
        GuardViolation: When the marker is missing or incorrect.
    """
    LOGGER.info(
        "validate_isolated_marker called marker_len=%s",
        len(marker),
    )
    if marker != REQUIRED_MARKER_VALUE:
        raise GuardViolation(
            f"isolated marker must be exactly {REQUIRED_MARKER_VALUE!r}",
            code="marker_invalid",
        )


def validate_database_name(database_name: str) -> None:
    """Require a load-specific database name token.

    Args:
        database_name: Declared Postgres database for the isolated target.

    Raises:
        GuardViolation: When the database name is not load-specific.
    """
    LOGGER.info("validate_database_name called database_name=%s", database_name)
    if not database_name.strip():
        raise GuardViolation("database name must be non-empty", code="database_empty")
    if not LOAD_DB_NAME_PATTERN.search(database_name):
        raise GuardViolation(
            "database name must include load/loadtest/load_test",
            code="database_not_load_specific",
        )


def validate_data_dir(data_dir: Path, repo_root: Path) -> None:
    """Require DATA_DIR to stay outside the repository runtime tree.

    Args:
        data_dir: Declared isolated runtime data directory.
        repo_root: Repository root for comparison.

    Raises:
        GuardViolation: When the directory equals repo ``data/``.
    """
    LOGGER.info("validate_data_dir called data_dir=%s repo_root=%s", data_dir, repo_root)
    if is_repo_data_dir(data_dir, repo_root):
        raise GuardViolation(
            "data_dir must not equal or nest under repository data/",
            code="data_dir_not_isolated",
        )


def validate_target_url(target_url: str) -> None:
    """Require a loopback or private-network HTTP(S) target.

    Args:
        target_url: Base URL for API calls.

    Raises:
        GuardViolation: When the host is public or unsupported.
    """
    LOGGER.info("validate_target_url called url_length=%s", len(target_url))
    parsed = urlparse(target_url)
    if parsed.scheme not in {"http", "https"}:
        raise GuardViolation("target URL must use http or https", code="target_scheme")
    if not is_private_or_loopback_host(parsed.hostname):
        raise GuardViolation(
            "target host must be loopback or private-network",
            code="target_not_private",
        )


def validate_database_dsn(database_dsn: str, database_name: str) -> None:
    """Require a private Postgres DSN whose database matches configuration.

    Args:
        database_dsn: Postgres connection string used by direct samplers.
        database_name: Expected isolated load database name.

    Raises:
        GuardViolation: When the DSN scheme, host, or database is unsafe.
    """
    LOGGER.info(
        "validate_database_dsn called dsn_length=%s database_name=%s",
        len(database_dsn),
        database_name,
    )
    parsed = urlparse(database_dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise GuardViolation(
            "database_dsn must use postgres or postgresql",
            code="database_dsn_scheme",
        )
    if not is_private_or_loopback_host(parsed.hostname):
        raise GuardViolation(
            "database_dsn host must be loopback or private-network",
            code="database_dsn_not_private",
        )
    dsn_db = parsed.path.lstrip("/")
    if dsn_db != database_name:
        raise GuardViolation(
            "database_dsn database name must match database_name",
            code="database_dsn_mismatch",
        )


def validate_remote_attestation(
    health: dict[str, object],
    config: LoadConfig,
) -> None:
    """Verify the live API identifies the configured isolated instance.

    Args:
        health: Parsed ``GET /api/health`` response.
        config: Guarded local load configuration.

    Raises:
        GuardViolation: When health, marker, or database attestation differs.
    """
    LOGGER.info(
        "validate_remote_attestation called status=%s environment=%s marker_present=%s "
        "database_name=%s",
        health.get("status"),
        health.get("environment"),
        bool(health.get("instance_marker")),
        health.get("database_name"),
    )
    if health.get("status") != "ok" or health.get("database") != "connected":
        raise GuardViolation(
            "target health is not ready for isolated load",
            code="remote_health_not_ready",
        )
    if health.get("environment") != REQUIRED_ENVIRONMENT:
        raise GuardViolation(
            f"target environment must be {REQUIRED_ENVIRONMENT!r}",
            code="remote_environment_mismatch",
        )
    if health.get("instance_marker") != config.isolated_marker:
        raise GuardViolation(
            "target instance_marker does not match configured isolated marker",
            code="remote_marker_mismatch",
        )
    if health.get("database_name") != config.database_name:
        raise GuardViolation(
            "target database_name does not match configured load database",
            code="remote_database_mismatch",
        )


def validate_live_database_snapshot(
    snapshot: dict[str, object],
    config: LoadConfig,
) -> None:
    """Require direct database sampling to attest the configured database.

    Args:
        snapshot: Combined pre-scenario sampler snapshot.
        config: Guarded live load configuration.

    Raises:
        GuardViolation: When direct DB metrics are unavailable or connected to
            a different database.
    """
    LOGGER.info(
        "validate_live_database_snapshot called expected_database=%s",
        config.database_name,
    )
    database = snapshot.get("database")
    if not isinstance(database, Mapping) or database.get("database_sampler") == "unavailable":
        raise GuardViolation(
            "direct database sampling must be available for measured live runs",
            code="live_database_sampler_unavailable",
        )
    current_database = database.get("current_database")
    if current_database != config.database_name:
        raise GuardViolation(
            "direct database sampler current_database does not match configuration",
            code="live_database_attestation_mismatch",
        )


def validate_bounds(config: LoadConfig) -> None:
    """Ensure scenario bounds remain finite and non-negative.

    Args:
        config: Loaded configuration.

    Raises:
        GuardViolation: When any bound is invalid.
    """
    LOGGER.info("validate_bounds called client_count=%s", config.client_count)
    if config.client_count < 1:
        raise GuardViolation("client_count must be >= 1", code="client_count")
    if config.max_concurrent_request_workers < 1:
        raise GuardViolation(
            "max_concurrent_request_workers must be >= 1",
            code="concurrent_workers",
        )
    if config.max_concurrent_request_workers > config.client_count:
        raise GuardViolation(
            "max_concurrent_request_workers must be <= client_count",
            code="concurrent_workers_exceed_clients",
        )
    if config.max_concurrent_request_workers > MAX_CONCURRENT_REQUEST_WORKERS:
        raise GuardViolation(
            f"max_concurrent_request_workers must be <= {MAX_CONCURRENT_REQUEST_WORKERS}",
            code="concurrent_workers_limit",
        )
    if config.poll_interval_s <= 0:
        raise GuardViolation("poll_interval_s must be > 0", code="poll_interval")
    if config.duration_s <= 0:
        raise GuardViolation("duration_s must be > 0", code="duration")
    if config.max_actions_per_client < 0:
        raise GuardViolation("max_actions_per_client must be >= 0", code="actions_bound")
    if config.max_uploads_per_client < 0:
        raise GuardViolation("max_uploads_per_client must be >= 0", code="uploads_bound")
    if config.action_burst_size < 1:
        raise GuardViolation("action_burst_size must be >= 1", code="burst_size")
    if config.burst_mode not in {"jitter", "sync"}:
        raise GuardViolation("burst_mode must be jitter or sync", code="burst_mode")
    if config.worker_fail_rate < 0 or config.worker_fail_rate > 1:
        raise GuardViolation("worker_fail_rate must be between 0 and 1", code="worker_fail_rate")


def validate_mutating_allowed(config: LoadConfig) -> None:
    """Require the isolated marker before any mutating scenario is permitted.

    Args:
        config: Loaded configuration.

    Raises:
        GuardViolation: When mutating features are enabled without the marker.
    """
    LOGGER.info(
        "validate_mutating_allowed called enable_actions=%s enable_uploads=%s seed_clients=%s",
        config.enable_actions,
        config.enable_uploads,
        config.seed_clients,
    )
    mutating = config.enable_actions or config.enable_uploads or config.seed_clients > 0
    if mutating:
        validate_isolated_marker(config.isolated_marker)


def validate_live_prerequisites(config: LoadConfig) -> None:
    """Require direct database access for a measured live run.

    Args:
        config: Guarded load configuration.

    Raises:
        GuardViolation: When the direct sampler DSN is omitted.
    """
    LOGGER.info(
        "validate_live_prerequisites called database_dsn_set=%s",
        bool(config.database_dsn),
    )
    if not config.database_dsn:
        raise GuardViolation(
            "measured live runs require database_dsn",
            code="live_database_dsn_required",
        )


def validate_config(config: LoadConfig, *, require_marker: bool = True) -> None:
    """Run all safety checks for a load configuration.

    Args:
        config: Loaded configuration to validate.
        require_marker: When ``True``, enforce the isolated marker even for
            read-only polling scenarios.

    Raises:
        GuardViolation: When any guard fails.
    """
    LOGGER.info(
        "validate_config called require_marker=%s dry_run=%s config_check=%s",
        require_marker,
        config.dry_run,
        config.config_check,
    )
    validate_target_url(config.target_url)
    validate_database_name(config.database_name)
    validate_data_dir(config.data_dir, config.repo_root)
    validate_bounds(config)
    if require_marker:
        validate_isolated_marker(config.isolated_marker)
    if config.database_dsn:
        validate_database_dsn(config.database_dsn, config.database_name)
    validate_mutating_allowed(config)
    LOGGER.info("validate_config completed")
