"""Fail-closed safety guards for the Wave 2 isolated end-to-end gate.

Validates isolated database naming, explicit ``DATA_DIR`` markers, private
database hosts, absent paid-GenAI credentials, and exact fake-triage controls
before any Postgres mutation, HTTP traffic, or worker execution is attempted.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from tests.integration.config import (
    FORBIDDEN_DATABASE_NAME,
    ISOLATED_MARKER_FILENAME,
    REQUIRED_ENVIRONMENT,
    REQUIRED_INSTANCE_MARKER,
    REPO_DATA_DIR_NAME,
    Wave2E2EConfig,
)

LOGGER = logging.getLogger(__name__)

ISOLATED_DB_NAME_PATTERN = re.compile(r"(test|isolated)", re.IGNORECASE)
PRIVATE_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}


class GuardViolation(Exception):
    """Raised when an end-to-end run fails a safety precondition."""

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
        host: Parsed hostname from a URL or DSN.

    Returns:
        ``True`` when the host is considered safe for isolated integration.
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
    repo_data = (repo_root / REPO_DATA_DIR_NAME).resolve()
    candidate = data_dir.resolve()
    return candidate == repo_data or repo_data in candidate.parents


def validate_database_name(database_name: str) -> None:
    """Require an isolated database name and forbid the live default.

    Args:
        database_name: Declared Postgres database for the isolated target.

    Raises:
        GuardViolation: When the database name is unsafe.
    """
    LOGGER.info("validate_database_name called database_name=%s", database_name)
    normalized = database_name.strip().lower()
    if not normalized:
        raise GuardViolation("database name must be non-empty", code="database_empty")
    if normalized == FORBIDDEN_DATABASE_NAME:
        raise GuardViolation(
            f"database name must not be {FORBIDDEN_DATABASE_NAME!r}",
            code="database_live_default",
        )
    if not ISOLATED_DB_NAME_PATTERN.search(database_name):
        raise GuardViolation(
            "database name must include test or isolated",
            code="database_not_isolated",
        )


def validate_database_url(database_url: str, database_name: str) -> None:
    """Require a private Postgres DSN whose database matches configuration.

    Args:
        database_url: Postgres connection string.
        database_name: Expected isolated database name.

    Raises:
        GuardViolation: When the DSN scheme, host, or database is unsafe.
    """
    LOGGER.info(
        "validate_database_url called url_length=%s database_name=%s",
        len(database_url),
        database_name,
    )
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise GuardViolation(
            "database URL must use postgres or postgresql",
            code="database_scheme",
        )
    if not is_private_or_loopback_host(parsed.hostname):
        raise GuardViolation(
            "database host must be loopback or private-network",
            code="database_host_not_private",
        )
    dsn_db = parsed.path.lstrip("/")
    if dsn_db != database_name:
        raise GuardViolation(
            "database URL path must match derived database name",
            code="database_name_mismatch",
        )


def validate_data_dir(data_dir: Path, repo_root: Path) -> None:
    """Require DATA_DIR outside the repository tree with an explicit marker.

    Args:
        data_dir: Declared isolated runtime data directory.
        repo_root: Repository root for comparison.

    Raises:
        GuardViolation: When the directory is unsafe or not explicitly isolated.
    """
    LOGGER.info("validate_data_dir called data_dir=%s repo_root=%s", data_dir, repo_root)
    if is_repo_data_dir(data_dir, repo_root):
        raise GuardViolation(
            "data_dir must not equal or nest under repository data/",
            code="data_dir_not_isolated",
        )
    marker = data_dir / ISOLATED_MARKER_FILENAME
    if not marker.is_file():
        raise GuardViolation(
            f"data_dir must contain marker file {ISOLATED_MARKER_FILENAME!r}",
            code="data_dir_marker_missing",
        )


def validate_no_paid_gemini_credentials() -> None:
    """Fail closed when a Gemini API key is present in the environment.

    Raises:
        GuardViolation: When ``GEMINI_API_KEY`` is configured.
    """
    LOGGER.info("validate_no_paid_gemini_credentials called")
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        raise GuardViolation(
            "GEMINI_API_KEY must be unset for isolated end-to-end runs",
            code="paid_gemini_possible",
        )


def validate_fake_worker_controls() -> None:
    """Require the exact fake-triage isolation triple in the environment.

    The runner injects these values into worker subprocesses; this guard ensures
    the operator shell is not configured for partial or unsafe fake mode.

    Raises:
        GuardViolation: When any control differs from the isolated contract.
    """
    LOGGER.info("validate_fake_worker_controls called")
    environment = os.getenv("APP_ENVIRONMENT", "").strip()
    marker = os.getenv("INSTANCE_MARKER", "").strip()
    fake_enabled = os.getenv("WORKER_FAKE_GEMINI", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if environment != REQUIRED_ENVIRONMENT:
        raise GuardViolation(
            f"APP_ENVIRONMENT must be {REQUIRED_ENVIRONMENT!r}",
            code="environment_invalid",
        )
    if marker != REQUIRED_INSTANCE_MARKER:
        raise GuardViolation(
            f"INSTANCE_MARKER must be {REQUIRED_INSTANCE_MARKER!r}",
            code="marker_invalid",
        )
    if not fake_enabled:
        raise GuardViolation(
            "WORKER_FAKE_GEMINI must be true for isolated end-to-end runs",
            code="fake_gemini_disabled",
        )


def validate_remote_attestation(health: dict[str, object], config: Wave2E2EConfig) -> None:
    """Verify the live API identifies the configured isolated instance.

    Args:
        health: Parsed ``GET /api/health`` response.
        config: Guarded local end-to-end configuration.

    Raises:
        GuardViolation: When health, marker, or database attestation differs.
    """
    LOGGER.info(
        "validate_remote_attestation called status=%s environment=%s database_name=%s",
        health.get("status"),
        health.get("environment"),
        health.get("database_name"),
    )
    if health.get("status") != "ok" or health.get("database") != "connected":
        raise GuardViolation(
            "target health is not ready for isolated end-to-end",
            code="remote_health_not_ready",
        )
    if health.get("environment") != REQUIRED_ENVIRONMENT:
        raise GuardViolation(
            f"target environment must be {REQUIRED_ENVIRONMENT!r}",
            code="remote_environment_mismatch",
        )
    if health.get("instance_marker") != REQUIRED_INSTANCE_MARKER:
        raise GuardViolation(
            "target instance_marker does not match required isolated marker",
            code="remote_marker_mismatch",
        )
    if health.get("database_name") != config.database_name:
        raise GuardViolation(
            "target database_name does not match configured isolated database",
            code="remote_database_mismatch",
        )


def validate_config(config: Wave2E2EConfig) -> None:
    """Run all safety checks for an end-to-end configuration.

    Args:
        config: Loaded configuration to validate.

    Raises:
        GuardViolation: When any guard fails.
    """
    LOGGER.info(
        "validate_config called database_name=%s data_dir=%s",
        config.database_name,
        config.data_dir,
    )
    validate_database_name(config.database_name)
    validate_database_url(config.database_url, config.database_name)
    validate_data_dir(config.data_dir, config.repo_root)
    validate_no_paid_gemini_credentials()
    validate_fake_worker_controls()
    LOGGER.info("validate_config completed")
