"""Matchmaking endpoint: enqueue and claim a compatible partner."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.api.deps import get_current_player, get_game_service, raise_game_error
from app.game.service import GameError, GameService
from app.game.types import PlayerRecord

logger = logging.getLogger(__name__)

router = APIRouter(tags=["game"])


@router.post("/api/pair/request")
async def pair_request(
    player: PlayerRecord = Depends(get_current_player),
    service: GameService = Depends(get_game_service),
) -> dict[str, str]:
    """Enter the matchmaking queue and attempt a transactional match.

    Args:
        player: Authenticated player from the Bearer token.
        service: Injected game service.

    Returns:
        ``{"status": "queued"}`` or ``{"status": "matched"}``.

    Side effects:
        May create a pair and first turn when a partner is claimed via
        ``FOR UPDATE SKIP LOCKED`` semantics in the Postgres store.
    """
    logger.info("pair_request called player_id=%s", player.id)
    try:
        result = await service.request_pair(player)
    except GameError as exc:
        raise_game_error(exc)
    logger.info(
        "pair_request completed player_id=%s status=%s",
        player.id,
        result.get("status"),
    )
    return result
