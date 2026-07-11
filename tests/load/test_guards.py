"""Safety guard tests for isolated load targeting."""

from __future__ import annotations

import logging

import pytest

from tools.load.config import REQUIRED_MARKER_VALUE, LoadConfig
from tools.load.guards import GuardViolation, validate_config, validate_target_url

LOGGER = logging.getLogger(__name__)


def test_public_target_is_rejected(safe_config: LoadConfig) -> None:
    """Reject non-private target hosts before any run."""
    LOGGER.info("test_public_target_is_rejected called")
    config = LoadConfig(
        **{
            **safe_config.__dict__,
            "target_url": "https://example.com",
        }
    )
    with pytest.raises(GuardViolation) as exc:
        validate_config(config)
    assert exc.value.code == "target_not_private"


def test_repo_data_dir_is_rejected(safe_config: LoadConfig, repo_root) -> None:
    """Reject repository ``data/`` as the load DATA_DIR."""
    LOGGER.info("test_repo_data_dir_is_rejected called")
    config = LoadConfig(
        **{
            **safe_config.__dict__,
            "data_dir": repo_root / "data",
        }
    )
    with pytest.raises(GuardViolation) as exc:
        validate_config(config)
    assert exc.value.code == "data_dir_not_isolated"


def test_non_load_database_name_is_rejected(safe_config: LoadConfig) -> None:
    """Reject production-like database names."""
    LOGGER.info("test_non_load_database_name_is_rejected called")
    config = LoadConfig(
        **{
            **safe_config.__dict__,
            "database_name": "dialect_factory",
        }
    )
    with pytest.raises(GuardViolation) as exc:
        validate_config(config)
    assert exc.value.code == "database_not_load_specific"


def test_missing_marker_is_rejected(safe_config: LoadConfig) -> None:
    """Require the explicit isolated marker."""
    LOGGER.info("test_missing_marker_is_rejected called")
    config = LoadConfig(
        **{
            **safe_config.__dict__,
            "isolated_marker": "",
        }
    )
    with pytest.raises(GuardViolation) as exc:
        validate_config(config)
    assert exc.value.code == "marker_invalid"


def test_mutating_without_marker_is_rejected(safe_config: LoadConfig) -> None:
    """Block seeding/actions/uploads unless the marker matches."""
    LOGGER.info("test_mutating_without_marker_is_rejected called")
    config = LoadConfig(
        **{
            **safe_config.__dict__,
            "isolated_marker": "wrong-marker",
            "seed_clients": 2,
        }
    )
    with pytest.raises(GuardViolation) as exc:
        validate_config(config)
    assert exc.value.code == "marker_invalid"


def test_private_loopback_hosts_are_allowed() -> None:
    """Accept localhost and RFC1918 hosts."""
    LOGGER.info("test_private_loopback_hosts_are_allowed called")
    for host in (
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://10.0.0.12:8000",
        "http://192.168.1.20:8000",
    ):
        validate_target_url(host)


def test_remote_database_dsn_host_is_rejected(safe_config: LoadConfig) -> None:
    """Reject a sampler DSN that points at a public database host."""
    LOGGER.info("test_remote_database_dsn_host_is_rejected called")
    config = LoadConfig(
        **{
            **safe_config.__dict__,
            "database_dsn": (
                "postgresql://load:redacted@db.example.com:5432/"
                "dialect_factory_load_test"
            ),
        }
    )
    with pytest.raises(GuardViolation) as exc:
        validate_config(config)
    assert exc.value.code == "database_dsn_not_private"


def test_concurrent_workers_cannot_exceed_clients(safe_config: LoadConfig) -> None:
    """Reject request-worker concurrency above simulated client count."""
    LOGGER.info("test_concurrent_workers_cannot_exceed_clients called")
    config = LoadConfig(
        **{
            **safe_config.__dict__,
            "client_count": 10,
            "max_concurrent_request_workers": 11,
        }
    )
    with pytest.raises(GuardViolation) as exc:
        validate_config(config)
    assert exc.value.code == "concurrent_workers_exceed_clients"


def test_concurrent_workers_have_finite_upper_bound(safe_config: LoadConfig) -> None:
    """Reject request-worker concurrency above the global safety ceiling."""
    LOGGER.info("test_concurrent_workers_have_finite_upper_bound called")
    config = LoadConfig(
        **{
            **safe_config.__dict__,
            "client_count": 2_000,
            "max_concurrent_request_workers": 1_001,
        }
    )
    with pytest.raises(GuardViolation) as exc:
        validate_config(config)
    assert exc.value.code == "concurrent_workers_limit"


def test_marker_exact_value_required() -> None:
    """Document the required marker constant."""
    LOGGER.info("test_marker_exact_value_required called")
    assert REQUIRED_MARKER_VALUE == "wave2-load-isolated"
