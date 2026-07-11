"""Static guard refusal tests for the Wave 2 isolated end-to-end gate.

These tests always run under ordinary pytest and never open Postgres, start
Docker, or invoke paid GenAI.
"""

from __future__ import annotations

import logging

import pytest

from tests.integration.config import (
    FORBIDDEN_DATABASE_NAME,
    REQUIRED_ENVIRONMENT,
    REQUIRED_INSTANCE_MARKER,
    Wave2E2EConfig,
)
from tests.integration.guards import GuardViolation, validate_config

LOGGER = logging.getLogger(__name__)


def test_live_database_name_is_rejected(safe_config: Wave2E2EConfig) -> None:
    """Reject the default development database name."""
    LOGGER.info("test_live_database_name_is_rejected called")
    config = Wave2E2EConfig(
        **{
            **safe_config.__dict__,
            "database_url": "postgresql://dialect:pw@127.0.0.1:5432/dialect_factory",
            "database_name": FORBIDDEN_DATABASE_NAME,
        }
    )
    with pytest.raises(GuardViolation) as caught:
        validate_config(config)
    assert caught.value.code == "database_live_default"
    LOGGER.info("test_live_database_name_is_rejected completed")


def test_non_isolated_database_name_is_rejected(safe_config: Wave2E2EConfig) -> None:
    """Reject database names without test/isolated tokens."""
    LOGGER.info("test_non_isolated_database_name_is_rejected called")
    config = Wave2E2EConfig(
        **{
            **safe_config.__dict__,
            "database_url": "postgresql://dialect:pw@127.0.0.1:5432/dialect_factory_pilot",
            "database_name": "dialect_factory_pilot",
        }
    )
    with pytest.raises(GuardViolation) as caught:
        validate_config(config)
    assert caught.value.code == "database_not_isolated"
    LOGGER.info("test_non_isolated_database_name_is_rejected completed")


def test_repo_data_dir_is_rejected(safe_config: Wave2E2EConfig, repo_root) -> None:
    """Reject repository ``data/`` as the integration DATA_DIR."""
    LOGGER.info("test_repo_data_dir_is_rejected called")
    config = Wave2E2EConfig(
        **{
            **safe_config.__dict__,
            "data_dir": repo_root / "data",
        }
    )
    with pytest.raises(GuardViolation) as caught:
        validate_config(config)
    assert caught.value.code == "data_dir_not_isolated"
    LOGGER.info("test_repo_data_dir_is_rejected completed")


def test_missing_isolated_marker_is_rejected(safe_config: Wave2E2EConfig, tmp_path) -> None:
    """Reject DATA_DIR directories without ``.neva-isolated``."""
    LOGGER.info("test_missing_isolated_marker_is_rejected called")
    unmarked = tmp_path / "unmarked"
    unmarked.mkdir()
    config = Wave2E2EConfig(
        **{
            **safe_config.__dict__,
            "data_dir": unmarked,
        }
    )
    with pytest.raises(GuardViolation) as caught:
        validate_config(config)
    assert caught.value.code == "data_dir_marker_missing"
    LOGGER.info("test_missing_isolated_marker_is_rejected completed")


def test_remote_database_host_is_rejected(safe_config: Wave2E2EConfig) -> None:
    """Reject a database URL that points at a public host."""
    LOGGER.info("test_remote_database_host_is_rejected called")
    config = Wave2E2EConfig(
        **{
            **safe_config.__dict__,
            "database_url": "postgresql://dialect:pw@db.example.com:5432/dialect_factory_isolated",
            "database_name": "dialect_factory_isolated",
        }
    )
    with pytest.raises(GuardViolation) as caught:
        validate_config(config)
    assert caught.value.code == "database_host_not_private"
    LOGGER.info("test_remote_database_host_is_rejected completed")


def test_paid_gemini_key_is_rejected(safe_config: Wave2E2EConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed when GEMINI_API_KEY is configured."""
    LOGGER.info("test_paid_gemini_key_is_rejected called")
    monkeypatch.setenv("GEMINI_API_KEY", "secret-key")
    with pytest.raises(GuardViolation) as caught:
        validate_config(safe_config)
    assert caught.value.code == "paid_gemini_possible"
    LOGGER.info("test_paid_gemini_key_is_rejected completed")


def test_partial_fake_controls_are_rejected(safe_config: Wave2E2EConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject load-test environment without the exact isolated marker."""
    LOGGER.info("test_partial_fake_controls_are_rejected called")
    monkeypatch.setenv("APP_ENVIRONMENT", REQUIRED_ENVIRONMENT)
    monkeypatch.setenv("INSTANCE_MARKER", "")
    monkeypatch.setenv("WORKER_FAKE_GEMINI", "true")
    with pytest.raises(GuardViolation) as caught:
        validate_config(safe_config)
    assert caught.value.code == "marker_invalid"
    LOGGER.info("test_partial_fake_controls_are_rejected completed")


def test_safe_configuration_is_accepted(safe_config: Wave2E2EConfig) -> None:
    """Accept a fully guarded isolated configuration."""
    LOGGER.info("test_safe_configuration_is_accepted called")
    validate_config(safe_config)
    assert REQUIRED_INSTANCE_MARKER == "wave2-load-isolated"
    assert REQUIRED_ENVIRONMENT == "load-test"
    LOGGER.info("test_safe_configuration_is_accepted completed")
