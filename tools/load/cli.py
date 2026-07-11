"""CLI argument parsing for the load harness."""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from tools.load.config import (
    DEFAULT_ACTION_BURST_SIZE,
    DEFAULT_CLIENT_COUNT,
    DEFAULT_DATABASE_NAME,
    DEFAULT_DATA_DIR,
    DEFAULT_DURATION_S,
    DEFAULT_JITTER_MAX_S,
    DEFAULT_MAX_CONCURRENT_REQUEST_WORKERS,
    DEFAULT_MAX_ACTIONS_PER_CLIENT,
    DEFAULT_MAX_UPLOADS_PER_CLIENT,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_REQUEST_TIMEOUT_S,
    DEFAULT_SYNC_OFFSET_S,
    DEFAULT_TARGET_URL,
    REQUIRED_MARKER_VALUE,
)

LOGGER = logging.getLogger(__name__)


@dataclass
class CliArgs:
    """Normalized CLI arguments before ``LoadConfig`` construction."""

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
    data_dir: str
    request_timeout_s: float
    scenario: str
    seed_clients: int
    dry_run: bool
    config_check: bool
    repo_root: str
    enable_actions: bool
    enable_uploads: bool
    worker_delay_s: float
    worker_fail_rate: float


def build_parser() -> argparse.ArgumentParser:
    """Construct the load harness argument parser.

    Returns:
        Configured ``ArgumentParser``.
    """
    LOGGER.info("build_parser called")
    parser = argparse.ArgumentParser(
        prog="python -m tools.load",
        description="Isolated Wave 2 venue load harness",
    )
    parser.add_argument("--target-url", default=os.getenv("LOAD_TARGET_URL", DEFAULT_TARGET_URL))
    parser.add_argument("--client-count", type=int, default=int(os.getenv("LOAD_CLIENT_COUNT", DEFAULT_CLIENT_COUNT)))
    parser.add_argument(
        "--max-concurrent-request-workers",
        type=int,
        default=int(
            os.getenv(
                "LOAD_MAX_CONCURRENT_REQUEST_WORKERS",
                DEFAULT_MAX_CONCURRENT_REQUEST_WORKERS,
            )
        ),
    )
    parser.add_argument(
        "--poll-interval-s",
        type=float,
        default=float(os.getenv("LOAD_POLL_INTERVAL_S", DEFAULT_POLL_INTERVAL_S)),
    )
    parser.add_argument("--duration-s", type=float, default=float(os.getenv("LOAD_DURATION_S", DEFAULT_DURATION_S)))
    parser.add_argument(
        "--jitter-max-s",
        type=float,
        default=float(os.getenv("LOAD_JITTER_MAX_S", DEFAULT_JITTER_MAX_S)),
    )
    parser.add_argument(
        "--sync-offset-s",
        type=float,
        default=float(os.getenv("LOAD_SYNC_OFFSET_S", DEFAULT_SYNC_OFFSET_S)),
    )
    parser.add_argument("--burst-mode", choices=["jitter", "sync"], default=os.getenv("LOAD_BURST_MODE", "jitter"))
    parser.add_argument(
        "--action-burst-size",
        type=int,
        default=int(os.getenv("LOAD_ACTION_BURST_SIZE", DEFAULT_ACTION_BURST_SIZE)),
    )
    parser.add_argument(
        "--max-actions-per-client",
        type=int,
        default=int(os.getenv("LOAD_MAX_ACTIONS_PER_CLIENT", DEFAULT_MAX_ACTIONS_PER_CLIENT)),
    )
    parser.add_argument(
        "--max-uploads-per-client",
        type=int,
        default=int(os.getenv("LOAD_MAX_UPLOADS_PER_CLIENT", DEFAULT_MAX_UPLOADS_PER_CLIENT)),
    )
    parser.add_argument(
        "--marker",
        dest="isolated_marker",
        default=os.getenv("LOAD_ISOLATED_MARKER", ""),
        help=f"Required isolated marker; must equal {REQUIRED_MARKER_VALUE!r}",
    )
    parser.add_argument(
        "--database-name",
        default=os.getenv("LOAD_DATABASE_NAME", DEFAULT_DATABASE_NAME),
    )
    parser.add_argument("--database-dsn", default=os.getenv("LOAD_DATABASE_DSN"))
    parser.add_argument("--data-dir", default=os.getenv("LOAD_DATA_DIR", str(DEFAULT_DATA_DIR)))
    parser.add_argument(
        "--request-timeout-s",
        type=float,
        default=float(os.getenv("LOAD_REQUEST_TIMEOUT_S", DEFAULT_REQUEST_TIMEOUT_S)),
    )
    parser.add_argument(
        "--scenario",
        choices=["poll_storm", "parallel_poll_storm", "action_burst", "mixed"],
        default=os.getenv("LOAD_SCENARIO", "parallel_poll_storm"),
    )
    parser.add_argument("--seed-clients", type=int, default=int(os.getenv("LOAD_SEED_CLIENTS", "0")))
    parser.add_argument("--repo-root", default=os.getenv("LOAD_REPO_ROOT", str(Path.cwd())))
    parser.add_argument("--enable-actions", action="store_true", default=_env_bool("LOAD_ENABLE_ACTIONS"))
    parser.add_argument("--enable-uploads", action="store_true", default=_env_bool("LOAD_ENABLE_UPLOADS"))
    parser.add_argument(
        "--worker-delay-s",
        type=float,
        default=float(os.getenv("LOAD_WORKER_DELAY_S", "0")),
    )
    parser.add_argument(
        "--worker-fail-rate",
        type=float,
        default=float(os.getenv("LOAD_WORKER_FAIL_RATE", "0")),
    )
    parser.add_argument("--dry-run", action="store_true", default=_env_bool("LOAD_DRY_RUN"))
    parser.add_argument("--config-check", action="store_true", default=_env_bool("LOAD_CONFIG_CHECK"))
    return parser


def _env_bool(name: str) -> bool:
    """Parse a boolean env var for argparse defaults.

    Args:
        name: Environment variable name.

    Returns:
        Parsed boolean, defaulting to ``False``.
    """
    raw = os.getenv(name)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_cli_args(argv: list[str] | None = None) -> CliArgs:
    """Parse CLI arguments into a normalized dataclass.

    Args:
        argv: Optional argv excluding program name.

    Returns:
        Parsed ``CliArgs`` instance.
    """
    LOGGER.info("parse_cli_args called argv_provided=%s", argv is not None)
    parser = build_parser()
    namespace = parser.parse_args(argv)
    return CliArgs(
        target_url=namespace.target_url,
        client_count=namespace.client_count,
        max_concurrent_request_workers=namespace.max_concurrent_request_workers,
        poll_interval_s=namespace.poll_interval_s,
        duration_s=namespace.duration_s,
        jitter_max_s=namespace.jitter_max_s,
        sync_offset_s=namespace.sync_offset_s,
        burst_mode=namespace.burst_mode,
        action_burst_size=namespace.action_burst_size,
        max_actions_per_client=namespace.max_actions_per_client,
        max_uploads_per_client=namespace.max_uploads_per_client,
        isolated_marker=namespace.isolated_marker,
        database_name=namespace.database_name,
        database_dsn=namespace.database_dsn,
        data_dir=namespace.data_dir,
        request_timeout_s=namespace.request_timeout_s,
        scenario=namespace.scenario,
        seed_clients=namespace.seed_clients,
        dry_run=namespace.dry_run,
        config_check=namespace.config_check,
        repo_root=namespace.repo_root,
        enable_actions=namespace.enable_actions,
        enable_uploads=namespace.enable_uploads,
        worker_delay_s=namespace.worker_delay_s,
        worker_fail_rate=namespace.worker_fail_rate,
    )
