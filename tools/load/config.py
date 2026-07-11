"""Central configuration for the Wave 2 isolated load harness.

All client counts, timing knobs, safety metadata, and scenario bounds are
defined here and may be overridden with ``LOAD_*`` environment variables or CLI
flags. This module never loads application ``.env`` files.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

LOGGER = logging.getLogger(__name__)

REQUIRED_MARKER_VALUE = "wave2-load-isolated"
REQUIRED_ENVIRONMENT = "load-test"
DEFAULT_TARGET_URL = "http://127.0.0.1:8000"
DEFAULT_POLL_INTERVAL_S = 2.0
DEFAULT_DURATION_S = 30.0
DEFAULT_CLIENT_COUNT = 200
DEFAULT_MAX_CONCURRENT_REQUEST_WORKERS = 200
MAX_CONCURRENT_REQUEST_WORKERS = 1000
DEFAULT_ACTION_BURST_SIZE = 10
DEFAULT_MAX_UPLOADS_PER_CLIENT = 1
DEFAULT_MAX_ACTIONS_PER_CLIENT = 3
DEFAULT_JITTER_MAX_S = 0.5
DEFAULT_SYNC_OFFSET_S = 0.0
DEFAULT_REQUEST_TIMEOUT_S = 10.0
DEFAULT_DATABASE_NAME = "dialect_factory_load_test"
DEFAULT_DATA_DIR = Path("/tmp/dialect_factory_load_data")
REPO_DATA_DIR_NAME = "data"


@dataclass(frozen=True)
class LoadConfig:
    """Describe a guarded, reproducible load run."""

    target_url: str
    client_count: int
    max_concurrent_request_workers: int
    poll_interval_s: float
    duration_s: float
    jitter_max_s: float
    sync_offset_s: float
    burst_mode: str
    action_burst_size: int
    max_actions_per_client: int
    max_uploads_per_client: int
    isolated_marker: str
    database_name: str
    database_dsn: str | None
    data_dir: Path
    request_timeout_s: float
    scenario: str
    seed_clients: int
    dry_run: bool
    config_check: bool
    repo_root: Path
    enable_actions: bool
    enable_uploads: bool
    worker_delay_s: float
    worker_fail_rate: float


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable.

    Args:
        name: Environment variable name.
        default: Value when unset.

    Returns:
        Parsed boolean.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_config(argv: list[str] | None = None) -> LoadConfig:
    """Load load-harness settings from environment variables and optional CLI.

    Args:
        argv: Optional argv tail passed to the CLI parser. When ``None``, only
            environment variables and defaults are used.

    Returns:
        A validated ``LoadConfig`` instance.

    Side effects:
        Logs safe configuration metadata at INFO without secrets.
    """
    LOGGER.info("load_config called argv_provided=%s", argv is not None)
    from tools.load.cli import parse_cli_args

    args = parse_cli_args(argv)
    repo_root = Path(args.repo_root).resolve()
    config = LoadConfig(
        target_url=args.target_url,
        client_count=args.client_count,
        max_concurrent_request_workers=args.max_concurrent_request_workers,
        poll_interval_s=args.poll_interval_s,
        duration_s=args.duration_s,
        jitter_max_s=args.jitter_max_s,
        sync_offset_s=args.sync_offset_s,
        burst_mode=args.burst_mode,
        action_burst_size=args.action_burst_size,
        max_actions_per_client=args.max_actions_per_client,
        max_uploads_per_client=args.max_uploads_per_client,
        isolated_marker=args.isolated_marker,
        database_name=args.database_name,
        database_dsn=args.database_dsn,
        data_dir=Path(args.data_dir).resolve(),
        request_timeout_s=args.request_timeout_s,
        scenario=args.scenario,
        seed_clients=args.seed_clients,
        dry_run=args.dry_run,
        config_check=args.config_check,
        repo_root=repo_root,
        enable_actions=args.enable_actions,
        enable_uploads=args.enable_uploads,
        worker_delay_s=args.worker_delay_s,
        worker_fail_rate=args.worker_fail_rate,
    )
    LOGGER.info(
        "load_config completed target_host=%s client_count=%s scenario=%s dry_run=%s "
        "config_check=%s data_dir=%s database_name=%s marker_set=%s",
        urlparse(config.target_url).hostname,
        config.client_count,
        config.scenario,
        config.dry_run,
        config.config_check,
        config.data_dir,
        config.database_name,
        bool(config.isolated_marker),
    )
    return config


def config_log_meta(config: LoadConfig) -> dict[str, object]:
    """Return redacted configuration metadata for structured logs.

    Args:
        config: Loaded load configuration.

    Returns:
        Safe metadata dict without tokens, DSN credentials, or payload bytes.
    """
    LOGGER.info("config_log_meta called scenario=%s", config.scenario)
    parsed = urlparse(config.target_url)
    dsn_meta: dict[str, object] | None = None
    if config.database_dsn:
        dsn = urlparse(config.database_dsn)
        dsn_meta = {
            "scheme": dsn.scheme,
            "host": dsn.hostname,
            "port": dsn.port,
            "database": dsn.path.lstrip("/") or None,
        }
    return {
        "target_scheme": parsed.scheme,
        "target_host": parsed.hostname,
        "target_port": parsed.port,
        "client_count": config.client_count,
        "max_concurrent_request_workers": config.max_concurrent_request_workers,
        "poll_interval_s": config.poll_interval_s,
        "duration_s": config.duration_s,
        "burst_mode": config.burst_mode,
        "scenario": config.scenario,
        "database_name": config.database_name,
        "database_dsn_meta": dsn_meta,
        "data_dir": str(config.data_dir),
        "dry_run": config.dry_run,
        "config_check": config.config_check,
        "enable_actions": config.enable_actions,
        "enable_uploads": config.enable_uploads,
        "worker_delay_s": config.worker_delay_s,
        "worker_fail_rate": config.worker_fail_rate,
        "marker_matches_required": config.isolated_marker == REQUIRED_MARKER_VALUE,
    }
