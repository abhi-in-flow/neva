"""Unauthenticated venue metrics endpoint for pitch and TV ticker numbers.

Exposes frozen ``MetricsResponse`` fields computed from canonical Postgres
tables (turns, records, decks, api_calls). Does not invent cost or treat
mutable ``metrics_counters`` as truth. State polling is unaffected.
"""

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
    """Return canonical throughput metrics for the venue display and pitch.

    Exact field definitions:

    - ``validated_pairs``: turns with outcome ``validated``.
    - ``training_eligible_pairs``: records with ``training_eligible`` true.
    - ``languages`` / ``language_count``: normalized declared native languages
      of speakers on validated turns only. ``common_langs`` and unplayed
      registrations are excluded by query source, while native Assamese,
      Hindi, English, or any other tag remains countable.
    - ``gauntlet_pass_rate``: eligible records ÷ packaged validated records;
      null when the denominator is zero.
    - ``deck_images_per_minute`` / ``deck_cost_per_image_usd``: from the latest
      activated live deck ``generation_metrics``; null without evidence.
    - ``cost_per_validated_sample_usd``: latest live deck
      ``generation_metrics.total_cost_usd`` plus successful
      ``gauntlet_triage`` API-call costs, divided by validated pairs. It stays
      null until all validated turns are packaged, each packaged validated
      record has one successful priced triage call, and deck total evidence
      exists. Other API operations are excluded to avoid double-counting.

    Args:
        service: Injected game service.

    Returns:
        Frozen ``MetricsResponse`` with defensible aggregates only.

    Side effects:
        Read-only store query; INFO logs safe aggregate metadata only.
    """
    logger.info("metrics called")
    try:
        response = await service.metrics()
    except GameError as exc:
        raise_game_error(exc)
    logger.info(
        "metrics completed validated_pairs=%s training_eligible_pairs=%s "
        "language_count=%s gauntlet_pass_rate_present=%s cost_present=%s "
        "deck_ipm_present=%s deck_cost_present=%s",
        response.validated_pairs,
        response.training_eligible_pairs,
        response.language_count,
        response.gauntlet_pass_rate is not None,
        response.cost_per_validated_sample_usd is not None,
        response.deck_images_per_minute is not None,
        response.deck_cost_per_image_usd is not None,
    )
    return response
