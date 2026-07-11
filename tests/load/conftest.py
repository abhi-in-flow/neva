"""Shared fixtures for load harness unit tests."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tools.load.config import REQUIRED_MARKER_VALUE, LoadConfig

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """Create a fake repository root with a ``data/`` directory."""
    LOGGER.info("repo_root fixture setup temp_name=%s", tmp_path.name)
    (tmp_path / "data").mkdir()
    return tmp_path


@pytest.fixture
def isolated_data_dir(tmp_path: Path) -> Path:
    """Return an isolated data directory outside the fake repo tree."""
    path = tmp_path / "isolated_load_data"
    path.mkdir()
    return path


@pytest.fixture
def safe_config(repo_root: Path, isolated_data_dir: Path) -> LoadConfig:
    """Return a guarded configuration suitable for dry-run tests."""
    LOGGER.info("safe_config fixture setup")
    return LoadConfig(
        target_url="http://127.0.0.1:8000",
        client_count=200,
        max_concurrent_request_workers=200,
        poll_interval_s=2.0,
        duration_s=30.0,
        jitter_max_s=0.5,
        sync_offset_s=0.0,
        burst_mode="jitter",
        action_burst_size=10,
        max_actions_per_client=3,
        max_uploads_per_client=1,
        isolated_marker=REQUIRED_MARKER_VALUE,
        database_name="dialect_factory_load_test",
        database_dsn="postgresql://load:load@127.0.0.1:5432/dialect_factory_load_test",
        data_dir=isolated_data_dir,
        request_timeout_s=5.0,
        scenario="poll_storm",
        seed_clients=0,
        dry_run=True,
        config_check=False,
        repo_root=repo_root,
        enable_actions=False,
        enable_uploads=False,
        worker_delay_s=0.0,
        worker_fail_rate=0.0,
    )
