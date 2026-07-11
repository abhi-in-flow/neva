"""Unauthenticated venue metrics endpoint for pitch and TV ticker numbers."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.api.deps import get_game_service, raise_game_error
from app.game.service import GameError, GameService
from contracts.api_types import MetricsResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["game"])


@router.get("/api/metrics", response_model=MetricsResponse)
async def metrics(
    service: GameService = Depends(get_game_service),
) -> MetricsResponse:
    """Return throughput metrics for the venue display and pitch.

    Args:
        service: Injected game service.

    Returns:
        ``MetricsResponse`` with validated pair counts and language coverage.
        Cost/gauntlet/deck fields remain null until sibling components fill
        ``metrics_counters``.
    """
    logger.info("metrics called")
    try:
        response = await service.metrics()
    except GameError as exc:
        raise_game_error(exc)
    logger.info(
        "metrics completed validated_pairs=%s language_count=%s",
        response.validated_pairs,
        response.language_count,
    )
    return response
