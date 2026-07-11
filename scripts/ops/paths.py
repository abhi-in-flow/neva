"""Path safety and isolation validation for Wave 2 recovery operations.

Centralizes rules that refuse live development targets, require backup
destinations outside ``DATA_DIR``, and gate restore verification on explicit
isolated markers. Bash scripts call these helpers so pytest can cover refusal
logic without touching live services or runtime data.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

ISOLATED_ENV_FLAG = "NEVA_OPS_ISOLATED"
ISOLATED_MARKER_FILENAME = ".neva-isolated"
LIVE_DATA_DIR_DEFAULT = Path("data")
LIVE_DATABASE_DEFAULT = "dialect_factory"
ISOLATED_DATABASE_SUFFIX = "_isolated"
ISOLATED_COMPOSE_SUFFIX = "_isolated"


class OpsPathError(ValueError):
    """Raised when a backup or restore path violates recovery safety rules."""


def is_secret_filename(name: str) -> bool:
    """Return True for ``.env`` and every ``.env.*`` variant.

    Args:
        name: One filesystem path component.

    Returns:
        True when the filename is forbidden from backup artifacts.

    Side effects:
        None.
    """
    return name == ".env" or name.startswith(".env.")


def resolve_path(path: str | Path, *, base: Path | None = None) -> Path:
    """Resolve ``path`` to an absolute, normalized filesystem location.

    Args:
        path: Relative or absolute path supplied by an operator or test.
        base: Optional anchor for relative paths; defaults to the current
            working directory.

    Returns:
        Absolute resolved path with symlinks collapsed.

    Side effects:
        Logs safe path metadata at INFO without reading file contents.
    """
    anchor = base or Path.cwd()
    resolved = (anchor / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    logger.info("resolve_path called input=%s base=%s resolved=%s", path, anchor, resolved)
    return resolved


def is_path_inside(child: Path, parent: Path) -> bool:
    """Return True when ``child`` is equal to or nested under ``parent``.

    Args:
        child: Candidate path that may live inside ``parent``.
        parent: Directory boundary that must contain ``child``.

    Returns:
        True when ``child`` resolves under ``parent``.

    Side effects:
        None.
    """
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def database_name_from_url(database_url: str) -> str:
    """Extract the database name from a Postgres DSN without credentials.

    Args:
        database_url: Full async or sync Postgres connection URL.

    Returns:
        Database segment from the URL path.

    Side effects:
        Logs only the derived database name at INFO.
    """
    parsed = urlparse(database_url)
    database = (parsed.path or "").lstrip("/") or LIVE_DATABASE_DEFAULT
    logger.info("database_name_from_url called database=%s", database)
    return database


def validate_backup_destination(
    destination: Path,
    data_dir: Path,
    *,
    exists: bool,
) -> None:
    """Ensure a backup destination is safe to create or plan against.

    Args:
        destination: Intended backup root directory.
        data_dir: Live runtime-data root that must not contain backups.
        exists: Whether ``destination`` already exists on disk.

    Raises:
        OpsPathError: When the destination is inside ``data_dir`` or already
            exists for a mutating backup.

    Side effects:
        Logs validated metadata at INFO.
    """
    logger.info(
        "validate_backup_destination called destination=%s data_dir=%s exists=%s",
        destination,
        data_dir,
        exists,
    )
    if is_path_inside(destination, data_dir):
        raise OpsPathError("backup destination must be outside DATA_DIR")
    if exists:
        raise OpsPathError("backup destination already exists; refusing overwrite")
    if is_secret_filename(destination.name):
        raise OpsPathError("backup destination cannot be a secret file")


def validate_backup_source(data_dir: Path) -> None:
    """Require an existing runtime-data directory before backup planning.

    Args:
        data_dir: Source ``DATA_DIR`` resolved by the operator.

    Raises:
        OpsPathError: If the source is absent or not a directory.

    Side effects:
        Logs safe source-path metadata at INFO.
    """
    logger.info("validate_backup_source called data_dir=%s", data_dir)
    if not data_dir.exists():
        raise OpsPathError("backup source DATA_DIR does not exist")
    if not data_dir.is_dir():
        raise OpsPathError("backup source DATA_DIR is not a directory")


def is_live_data_dir(data_dir: Path, live_data_dir: Path | None = None) -> bool:
    """Return True when ``data_dir`` resolves to the configured live runtime root.

    Args:
        data_dir: Candidate runtime-data directory.
        live_data_dir: Explicit live root; defaults to ``./data`` resolved from cwd.

    Returns:
        True when the paths resolve to the same location.

    Side effects:
        None.
    """
    live_root = (live_data_dir or LIVE_DATA_DIR_DEFAULT).resolve()
    return data_dir.resolve() == live_root


def has_isolated_marker(data_dir: Path) -> bool:
    """Return True when ``data_dir`` contains the isolated marker file.

    Args:
        data_dir: Runtime-data directory under validation.

    Returns:
        True when ``.neva-isolated`` exists in ``data_dir``.

    Side effects:
        None.
    """
    return (data_dir / ISOLATED_MARKER_FILENAME).is_file()


def validate_restore_target(
    *,
    data_dir: Path,
    database_url: str,
    compose_project: str,
    isolated_env: str | None = None,
    live_data_dir: Path | None = None,
    live_database_url: str | None = None,
) -> None:
    """Refuse restore targets that are not explicitly marked isolated.

    Args:
        data_dir: Destination runtime-data directory for verification restore.
        database_url: Postgres DSN that would receive restored data.
        compose_project: Docker Compose project name for the target stack.
        isolated_env: Value of ``NEVA_OPS_ISOLATED``; defaults to ``os.environ``.
        live_data_dir: Live runtime root used for refusal checks.
        live_database_url: Live DSN used for refusal checks.

    Raises:
        OpsPathError: When any isolation requirement is missing or the target
            matches live development paths.

    Side effects:
        Logs safe validation metadata at INFO.
    """
    env_flag = isolated_env if isolated_env is not None else os.environ.get(ISOLATED_ENV_FLAG, "")
    database = database_name_from_url(database_url)
    live_db = database_name_from_url(live_database_url or os.environ.get("DATABASE_URL", ""))
    logger.info(
        "validate_restore_target called data_dir=%s database=%s compose_project=%s isolated_env=%s",
        data_dir,
        database,
        compose_project,
        bool(env_flag),
    )
    if env_flag not in {"1", "true", "TRUE", "yes", "YES"}:
        raise OpsPathError("restore verification requires NEVA_OPS_ISOLATED=1")
    if not has_isolated_marker(data_dir):
        raise OpsPathError(
            f"restore target DATA_DIR must contain marker file {ISOLATED_MARKER_FILENAME}",
        )
    if not compose_project.endswith(ISOLATED_COMPOSE_SUFFIX):
        raise OpsPathError("compose project name must end with _isolated")
    if not database.endswith(ISOLATED_DATABASE_SUFFIX):
        raise OpsPathError("database name must end with _isolated")
    if is_live_data_dir(data_dir, live_data_dir):
        raise OpsPathError("restore target DATA_DIR matches live development path")
    if live_db and database == live_db:
        raise OpsPathError("restore target database matches live development database")


def validate_restore_destination_empty(destination: Path) -> None:
    """Refuse restore when the destination directory already contains data.

    Args:
        destination: Directory that must be empty before restore. The isolated
            marker file ``.neva-isolated`` is permitted as the sole entry.

    Raises:
        OpsPathError: When ``destination`` exists and contains data beyond the
            isolated marker file.

    Side effects:
        Logs directory presence at INFO.
    """
    logger.info("validate_restore_destination_empty called destination=%s", destination)
    if not destination.exists():
        return
    entries = list(destination.iterdir())
    allowed_only = len(entries) == 1 and entries[0].name == ISOLATED_MARKER_FILENAME
    if entries and not allowed_only:
        raise OpsPathError("restore destination already contains data; refusing overwrite")


def runtime_subdirs(data_dir: Path) -> tuple[Path, ...]:
    """Return contract runtime subdirectories copied during backup.

    Args:
        data_dir: Root runtime directory from ``contracts/dirs.md``.

    Returns:
        Tuple of ``audio``, ``decks``, and ``corpus`` paths.

    Side effects:
        None.
    """
    return (
        data_dir / "audio",
        data_dir / "decks",
        data_dir / "corpus",
    )
