"""Join endpoint: create a player session and issue a bearer token."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.api.deps import get_game_service, raise_game_error
from app.game.service import GameError, GameService
from contracts.api_types import JoinRequest, JoinResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["game"])


@router.post("/api/join", response_model=JoinResponse)
async def join(
    body: JoinRequest,
    service: GameService = Depends(get_game_service),
) -> JoinResponse:
    """Register a player and return an opaque session token.

    Args:
        body: Nickname, native language, and common languages.
        service: Injected game service.

    Returns:
        ``JoinResponse`` with ``session_token`` for subsequent Bearer auth.

    Side effects:
        Inserts a player row; does not enqueue matchmaking.
    """
    logger.info(
        "join called nickname_len=%s native_lang=%s common_count=%s",
        len(body.nickname),
        body.native_lang,
        len(body.common_langs),
    )
    try:
        response = await service.join(
            nickname=body.nickname,
            native_lang=body.native_lang,
            common_langs=body.common_langs,
        )
    except GameError as exc:
        raise_game_error(exc)
    logger.info("join completed")
    return response
