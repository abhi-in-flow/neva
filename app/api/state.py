"""Polling state endpoint: composed player view for the React client."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.api.deps import get_current_player, get_game_service, raise_game_error
from app.game.service import GameError, GameService
from app.game.types import PlayerRecord
from contracts.api_types import StateResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["game"])


@router.get("/api/state", response_model=StateResponse)
async def get_state(
    player: PlayerRecord = Depends(get_current_player),
    service: GameService = Depends(get_game_service),
) -> StateResponse:
    """Return the full server-owned view-state for the authenticated player.

    Args:
        player: Authenticated player.
        service: Injected game service.

    Returns:
        ``StateResponse`` including phase, visibility-scoped turn payload,
        scores, and a compact leaderboard. Labels are omitted before accepted
        audio.
    """
    logger.info("get_state called player_id=%s", player.id)
    try:
        response = await service.get_state(player)
    except GameError as exc:
        raise_game_error(exc)
    logger.info(
        "get_state completed player_id=%s phase=%s version=%s",
        player.id,
        response.phase,
        response.state_version,
    )
    return response
