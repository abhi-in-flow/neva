"""Central configuration for the Wave 2 isolated end-to-end acceptance gate.

Loads operator-supplied ``WAVE2_E2E_*`` environment variables and repository
metadata. This module never reads application ``.env`` files so ordinary pytest
collection stays independent of local development credentials.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

LOGGER = logging.getLogger(__name__)

REQUIRED_ENVIRONMENT = "load-test"
REQUIRED_INSTANCE_MARKER = "wave2-load-isolated"
ISOLATED_MARKER_FILENAME = ".neva-isolated"
FORBIDDEN_DATABASE_NAME = "dialect_factory"
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 18_080
DEFAULT_API_PATH = "/api/health"
DEFAULT_WORKER_ID = "wave2-e2e-worker"
ENV_DATABASE_URL = "WAVE2_E2E_DATABASE_URL"
ENV_DATA_DIR = "WAVE2_E2E_DATA_DIR"
ENV_REQUIRE_FRONTEND = "WAVE2_E2E_REQUIRE_FRONTEND"
ENV_API_HOST = "WAVE2_E2E_API_HOST"
ENV_API_PORT = "WAVE2_E2E_API_PORT"
ENV_REPO_ROOT = "WAVE2_E2E_REPO_ROOT"
REPO_DATA_DIR_NAME = "data"


@dataclass(frozen=True)
class Wave2E2EConfig:
    """Describe one guarded isolated end-to-end acceptance run."""

    database_url: str
    database_name: str
    data_dir: Path
    repo_root: Path
    api_host: str
    api_port: int
    require_frontend: bool
    worker_id: str

    @property
    def api_base_url(self) -> str:
        """Return the HTTP base URL for API calls."""
        return f"http://{self.api_host}:{self.api_port}"

    @property
    def health_url(self) -> str:
        """Return the health probe URL used before scenario traffic."""
        return f"{self.api_base_url}{DEFAULT_API_PATH}"


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable.

    Args:
        name: Environment variable name.
        default: Value when unset.

    Returns:
        Parsed boolean.
    """
    LOGGER.info("_env_bool called name=%s default=%s", name, default)
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def database_name_from_url(database_url: str) -> str:
    """Extract the Postgres database segment from a DSN without credentials.

    Args:
        database_url: Full Postgres connection URL.

    Returns:
        Database name from the URL path.
    """
    LOGGER.info("database_name_from_url called url_length=%s", len(database_url))
    parsed = urlparse(database_url)
    return (parsed.path or "").lstrip("/") or FORBIDDEN_DATABASE_NAME


def resolve_repo_root() -> Path:
    """Resolve the repository root for guard comparisons.

    Returns:
        Absolute repository root, preferring ``WAVE2_E2E_REPO_ROOT`` when set.
    """
    LOGGER.info("resolve_repo_root called")
    override = os.getenv(ENV_REPO_ROOT)
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[2]


def load_config_from_env() -> Wave2E2EConfig | None:
    """Load a complete configuration when both required env vars are present.

    Returns:
        ``Wave2E2EConfig`` when ``WAVE2_E2E_DATABASE_URL`` and
        ``WAVE2_E2E_DATA_DIR`` are set; otherwise ``None``.
    """
    LOGGER.info("load_config_from_env called")
    database_url = os.getenv(ENV_DATABASE_URL, "").strip()
    data_dir_raw = os.getenv(ENV_DATA_DIR, "").strip()
    if not database_url or not data_dir_raw:
        LOGGER.info(
            "load_config_from_env incomplete database_set=%s data_dir_set=%s",
            bool(database_url),
            bool(data_dir_raw),
        )
        return None
    api_port_raw = os.getenv(ENV_API_PORT, str(DEFAULT_API_PORT)).strip()
    config = Wave2E2EConfig(
        database_url=database_url,
        database_name=database_name_from_url(database_url),
        data_dir=Path(data_dir_raw).resolve(),
        repo_root=resolve_repo_root(),
        api_host=os.getenv(ENV_API_HOST, DEFAULT_API_HOST).strip() or DEFAULT_API_HOST,
        api_port=int(api_port_raw),
        require_frontend=_env_bool(ENV_REQUIRE_FRONTEND, default=False),
        worker_id=DEFAULT_WORKER_ID,
    )
    LOGGER.info(
        "load_config_from_env completed database_name=%s data_dir=%s api_port=%s "
        "require_frontend=%s",
        config.database_name,
        config.data_dir,
        config.api_port,
        config.require_frontend,
    )
    return config


def config_log_meta(config: Wave2E2EConfig) -> dict[str, object]:
    """Return redacted configuration metadata for structured logs.

    Args:
        config: Loaded end-to-end configuration.

    Returns:
        Safe metadata without credentials or payload bytes.
    """
    LOGGER.info("config_log_meta called database_name=%s", config.database_name)
    parsed = urlparse(config.database_url)
    return {
        "database_host": parsed.hostname,
        "database_port": parsed.port,
        "database_name": config.database_name,
        "data_dir": str(config.data_dir),
        "repo_root": str(config.repo_root),
        "api_base_url": config.api_base_url,
        "require_frontend": config.require_frontend,
        "worker_id": config.worker_id,
        "environment": REQUIRED_ENVIRONMENT,
        "instance_marker": REQUIRED_INSTANCE_MARKER,
    }
