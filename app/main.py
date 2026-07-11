"""FastAPI application entrypoint for Dialect Data Factory Phase 0.

Exposes the health probe, static root placeholder, and media mount. On startup
the lifespan creates the runtime directories required by ``contracts/dirs.md``
and opens the Postgres pool; on shutdown it closes the pool cleanly. Game
routes are intentionally absent in Phase 0 — this module only proves the
Windows-hosted baseline.
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
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/media", StaticFiles(directory=DATA_DIR), name="media")
include_game_routers(app)


@app.get("/api/health")
async def health(request: Request) -> JSONResponse:
    """Probe API liveness and Postgres connectivity.

    Args:
        request: Incoming request used to reach the shared connection pool.

    Returns:
        JSON ``{"status":"ok","database":"connected"}`` when ``SELECT 1``
        succeeds against the pool.

    Side effects:
        Executes a single read-only SQL statement. Raises if the pool is
        unavailable or the query fails.
    """
    logger.info("health called")
    pool: asyncpg.Pool = request.app.state.pool
    await pool.fetchval("SELECT 1")
    logger.info("health completed database=connected")
    return JSONResponse({"status": "ok", "database": "connected"})


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    """Serve the Phase 0 static placeholder page.

    Returns:
        The ``app/static/index.html`` file response.

    Side effects:
        Reads the static file from disk.
    """
    logger.info("root called static_dir=%s", STATIC_DIR)
    return FileResponse(STATIC_DIR / "index.html")
