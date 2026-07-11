"""Shared fixtures for Wave 2 integration guard tests."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tests.integration.config import (
    ISOLATED_MARKER_FILENAME,
    REQUIRED_INSTANCE_MARKER,
    Wave2E2EConfig,
)

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """Create a fake repository root with a ``data/`` directory.

    Args:
        tmp_path: Pytest temporary directory root.

    Returns:
        Fake repository root path.
    """
    LOGGER.info("repo_root fixture setup")
    (tmp_path / "data").mkdir()
    return tmp_path


@pytest.fixture
def isolated_data_dir(tmp_path: Path) -> Path:
    """Return an isolated data directory with the required marker file.

    Args:
        tmp_path: Pytest temporary directory root.

    Returns:
        Marked isolated ``DATA_DIR`` outside the fake repository tree.
    """
    path = tmp_path / "isolated_e2e_data"
    path.mkdir()
    (path / ISOLATED_MARKER_FILENAME).write_text("wave-2 e2e isolated\n", encoding="utf-8")
    LOGGER.info("isolated_data_dir fixture created path=%s", path)
    return path


@pytest.fixture
def safe_config(repo_root: Path, isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Wave2E2EConfig:
    """Return a guarded configuration suitable for static guard tests.

    Args:
        repo_root: Fake repository root.
        isolated_data_dir: Marked isolated data directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        ``Wave2E2EConfig`` with safe isolated defaults.
    """
    LOGGER.info("safe_config fixture setup")
    monkeypatch.setenv("APP_ENVIRONMENT", "load-test")
    monkeypatch.setenv("INSTANCE_MARKER", REQUIRED_INSTANCE_MARKER)
    monkeypatch.setenv("WORKER_FAKE_GEMINI", "true")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return Wave2E2EConfig(
        database_url="postgresql://dialect:dialect_dev_only@127.0.0.1:5432/dialect_factory_isolated",
        database_name="dialect_factory_isolated",
        data_dir=isolated_data_dir,
        repo_root=repo_root,
        api_host="127.0.0.1",
        api_port=18_080,
        require_frontend=False,
        worker_id="wave2-e2e-worker",
    )
