"""Configuration loading tests for the load harness."""

from __future__ import annotations

import logging
from pathlib import Path

from tools.load.config import REQUIRED_MARKER_VALUE, load_config

LOGGER = logging.getLogger(__name__)


def test_load_config_reads_env_overrides(
    repo_root: Path,
    isolated_data_dir: Path,
    monkeypatch,
) -> None:
    """Environment variables override defaults safely."""
    LOGGER.info("test_load_config_reads_env_overrides called")
    monkeypatch.setenv("LOAD_TARGET_URL", "http://127.0.0.1:9001")
    monkeypatch.setenv("LOAD_CLIENT_COUNT", "50")
    monkeypatch.setenv("LOAD_ISOLATED_MARKER", REQUIRED_MARKER_VALUE)
    monkeypatch.setenv("LOAD_DATABASE_NAME", "dialect_factory_load_test")
    monkeypatch.setenv("LOAD_DATA_DIR", str(isolated_data_dir))
    monkeypatch.setenv("LOAD_REPO_ROOT", str(repo_root))
    config = load_config(["--dry-run"])
    assert config.target_url == "http://127.0.0.1:9001"
    assert config.client_count == 50
    assert config.dry_run is True


def test_default_scenario_uses_full_parallel_polling(
    repo_root: Path,
    isolated_data_dir: Path,
    monkeypatch,
) -> None:
    """Default to parallel polling with 200 centralized request workers."""
    LOGGER.info("test_default_scenario_uses_full_parallel_polling called")
    for name in (
        "LOAD_SCENARIO",
        "LOAD_CLIENT_COUNT",
        "LOAD_MAX_CONCURRENT_REQUEST_WORKERS",
    ):
        monkeypatch.delenv(name, raising=False)
    config = load_config(
        [
            "--dry-run",
            "--marker",
            REQUIRED_MARKER_VALUE,
            "--database-name",
            "dialect_factory_load_test",
            "--data-dir",
            str(isolated_data_dir),
            "--repo-root",
            str(repo_root),
        ]
    )
    assert config.scenario == "parallel_poll_storm"
    assert config.client_count == 200
    assert config.max_concurrent_request_workers == 200
