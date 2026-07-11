"""Unauthenticated venue leaderboard endpoint for the TV display."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_game_service, raise_game_error
from app.game.service import GameError, GameService
from contracts.api_types import LeaderboardResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["game"])


@router.get("/api/leaderboard", response_model=LeaderboardResponse)
async def leaderboard(
    top: int = Query(default=15, ge=1, le=100),
    service: GameService = Depends(get_game_service),
) -> LeaderboardResponse:
    """Return the top nicknames by validated-pair points.

    Args:
        top: Maximum rows to return.
        service: Injected game service.

    Returns:
        ``LeaderboardResponse`` ordered by score descending.
    """
    logger.info("leaderboard called top=%s", top)
    try:
        response = await service.leaderboard(top=top)
    except GameError as exc:
        raise_game_error(exc)
    logger.info("leaderboard completed entry_count=%s", len(response.entries))
    return response
