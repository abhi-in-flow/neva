"""FastAPI application entrypoint for Dialect Data Factory.

Exposes game, health, leaderboard, metrics, deck-administration, static-root,
and media routes. On startup the lifespan creates the runtime directories
required by ``contracts/dirs.md`` and opens the Postgres pool; on shutdown it
closes the pool cleanly. The entrypoint is deployed from WSL2/Linux and serves
the backend-owned game state contract.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routers import include_game_routers
from app.config import Settings, get_settings
from app.database import create_pool
from contracts.api_types import HealthResponse

logger = logging.getLogger(__name__)

RUNTIME_SUBDIRS = ("audio", "decks", "corpus")
APP_LOGGER_NAMES = (
    "app",
    "app.main",
    "app.api",
    "app.config",
    "app.database",
    "app.game",
    "scripts",
)


def configure_app_logging() -> None:
    """Ensure application loggers remain audible under uvicorn.

    Uvicorn's default dictConfig can leave import-time loggers without a
    reliable visible handler path during lifespan. This attaches a dedicated
    stderr handler on the ``app`` package logger so INFO call logs from owned
    modules are always emitted, without logging secrets.

    Returns:
        None.

    Side effects:
        Configures the ``app`` logger with an INFO stderr handler when missing
        and re-enables known application loggers.
    """
    app_root = logging.getLogger("app")
    app_root.setLevel(logging.INFO)
    app_root.disabled = False
    if not app_root.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s %(message)s"),
        )
        app_root.addHandler(handler)
    app_root.propagate = False
    for name in APP_LOGGER_NAMES:
        log = logging.getLogger(name)
        log.setLevel(logging.INFO)
        log.disabled = False
        if name != "app":
            log.propagate = True
    logger.info(
        "configure_app_logging called logger_count=%s handler_count=%s",
        len(APP_LOGGER_NAMES),
        len(app_root.handlers),
    )


def ensure_runtime_directories(settings: Settings) -> list[Path]:
    """Create the local runtime blob directories if they are missing.

    Args:
        settings: Application settings whose ``data_dir`` is the root.

    Returns:
        The list of directory paths that were ensured (root plus subdirs).

    Side effects:
        Creates ``data/``, ``data/audio/``, ``data/decks/``, and
        ``data/corpus/`` as needed. Does not delete or modify existing files.
    """
    logger.info(
        "ensure_runtime_directories called data_dir=%s subdirs=%s",
        settings.data_dir,
        RUNTIME_SUBDIRS,
    )
    created: list[Path] = []
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    created.append(settings.data_dir)
    for name in RUNTIME_SUBDIRS:
        path = settings.data_dir / name
        path.mkdir(exist_ok=True)
        created.append(path)
    logger.info(
        "ensure_runtime_directories completed path_count=%s",
        len(created),
    )
    return created


def frontend_index_path(settings: Settings) -> Path:
    """Resolve the Vite entrypoint or the development placeholder.

    Args:
        settings: Application settings containing the configured build root.

    Returns:
        The built ``index.html`` when present, otherwise the Phase 0
        placeholder. Production startup separately rejects the placeholder
        when ``frontend_required`` is true.
    """
    logger.info(
        "frontend_index_path called frontend_dist_dir=%s frontend_required=%s",
        settings.frontend_dist_dir,
        settings.frontend_required,
    )
    built_index = settings.frontend_dist_dir / "index.html"
    if built_index.is_file():
        return built_index
    return Path(__file__).parent / "static" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage FastAPI startup and shutdown resources.

    Args:
        app: The FastAPI application instance receiving ``state.pool``.

    Yields:
        Control to the running application after directories and the pool are
        ready.

    Side effects:
        Creates runtime directories, opens an asyncpg pool on ``app.state.pool``,
        and closes that pool on shutdown.
    """
    configure_app_logging()
    logger.info("lifespan startup begin")
    settings = get_settings()
    ensure_runtime_directories(settings)
    if settings.frontend_required and not (
        settings.frontend_dist_dir / "index.html"
    ).is_file():
        raise RuntimeError(
            "FRONTEND_REQUIRED is enabled but FRONTEND_DIST_DIR has no index.html"
        )
    app.state.pool = await create_pool(settings)
    logger.info("lifespan startup complete")
    try:
        yield
    finally:
        logger.info("lifespan shutdown begin")
        await app.state.pool.close()
        logger.info("lifespan shutdown complete")


app = FastAPI(title="Dialect Data Factory", version="0.1.0", lifespan=lifespan)

DATA_DIR = get_settings().data_dir
app.mount("/media", StaticFiles(directory=DATA_DIR), name="media")
include_game_routers(app)


@app.get("/api/health", response_model=HealthResponse)
async def health(request: Request) -> JSONResponse:
    """Probe API liveness and Postgres connectivity.

    Args:
        request: Incoming request used to reach the shared connection pool.

    Returns:
        Health payload with connectivity plus non-secret deployment identity.
        Load tooling uses the marker and database name to attest that traffic
        targets an isolated stack rather than trusting CLI declarations.

    Side effects:
        Executes a single read-only SQL statement. Raises if the pool is
        unavailable or the query fails.
    """
    logger.info("health called")
    pool: asyncpg.Pool = request.app.state.pool
    await pool.fetchval("SELECT 1")
    database_name = await pool.fetchval("SELECT current_database()")
    settings = get_settings()
    marker = settings.instance_marker or None
    logger.info(
        "health completed database=connected database_name=%s environment=%s "
        "instance_marker_set=%s",
        database_name,
        settings.app_environment,
        marker is not None,
    )
    return JSONResponse(
        {
            "status": "ok",
            "database": "connected",
            "environment": settings.app_environment,
            "instance_marker": marker,
            "database_name": database_name,
        }
    )


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    """Serve the built player application or development placeholder.

    Returns:
        The configured Vite ``index.html`` or the Phase 0 placeholder.

    Side effects:
        Reads the static file from disk.
    """
    settings = get_settings()
    index_path = frontend_index_path(settings)
    logger.info("root called index_path=%s", index_path)
    return FileResponse(index_path)


@app.get("/{frontend_path:path}", include_in_schema=False, response_model=None)
async def frontend_spa(frontend_path: str) -> FileResponse | JSONResponse:
    """Serve Vite assets and client routes, including the venue ``/tv`` view.

    Args:
        frontend_path: URL path not matched by API or media routes.

    Returns:
        A built static file when it exists, the SPA entrypoint for client
        routes, or a JSON 404 for unknown API/media paths.

    Side effects:
        Reads only files contained by ``frontend_dist_dir``.
    """
    logger.info(
        "frontend_spa called path_length=%s first_segment=%s",
        len(frontend_path),
        frontend_path.partition("/")[0],
    )
    if frontend_path == "api" or frontend_path.startswith("api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if frontend_path == "media" or frontend_path.startswith("media/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    settings = get_settings()
    dist_root = settings.frontend_dist_dir.resolve()
    candidate = (dist_root / frontend_path).resolve()
    if candidate.is_relative_to(dist_root) and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(frontend_index_path(settings))
