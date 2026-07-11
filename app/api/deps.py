"""FastAPI dependencies for game-core routers.

Resolves the shared ``GameService`` from application state (or builds one from
the asyncpg pool) and authenticates players via ``Authorization: Bearer``.
Tokens are never logged; only fingerprints appear in INFO logs.
"""

from __future__ import annotations

import logging
from typing import Annotated

import asyncpg
from fastapi import Depends, Header, HTTPException, Request

from app.config import get_settings
from app.game.config import get_game_config
from app.game.pg_store import PostgresGameStore
from app.game.service import GameError, GameService
from app.game.tokens import token_fingerprint
from app.game.types import PlayerRecord

logger = logging.getLogger(__name__)


def get_game_service(request: Request) -> GameService:
    """Return the process ``GameService``, creating a Postgres-backed one if needed.

    Args:
        request: Incoming request providing ``app.state``.

    Returns:
        Shared ``GameService`` instance.

    Side effects:
        May attach ``game_service`` on ``app.state`` the first time it is needed.
    """
    logger.info("get_game_service called")
    existing = getattr(request.app.state, "game_service", None)
    if existing is not None:
        return existing
    pool: asyncpg.Pool = request.app.state.pool
    settings = get_settings()
    service = GameService(
        PostgresGameStore(pool),
        data_dir=settings.data_dir,
        rounds_cap=settings.rounds_cap,
        config=get_game_config(),
    )
    request.app.state.game_service = service
    logger.info("get_game_service created postgres-backed service")
    return service


async def get_current_player(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    service: GameService = Depends(get_game_service),
) -> PlayerRecord:
    """Authenticate the caller from the Bearer session token.

    Args:
        request: Incoming request (unused except for logging context).
        authorization: Raw ``Authorization`` header value.
        service: Injected game service.

    Returns:
        Authenticated ``PlayerRecord``.

    Raises:
        HTTPException: 401 when the header or token is invalid.
    """
    _ = request
    logger.info(
        "get_current_player called has_authorization=%s",
        bool(authorization),
    )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authorization Bearer token required")
    token = authorization.split(" ", 1)[1].strip()
    try:
        player = await service.resolve_player(token)
    except GameError as exc:
        logger.info(
            "get_current_player rejected token_fp=%s status=%s",
            token_fingerprint(token) if token else "empty",
            exc.status_code,
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    logger.info("get_current_player completed player_id=%s", player.id)
    return player


def raise_game_error(exc: GameError) -> None:
    """Translate a domain ``GameError`` into an HTTPException.

    Args:
        exc: Domain error raised by ``GameService``.

    Raises:
        HTTPException: Always, with the domain status and detail.
    """
    logger.info(
        "raise_game_error called status_code=%s detail_len=%s",
        exc.status_code,
        len(exc.detail),
    )
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
