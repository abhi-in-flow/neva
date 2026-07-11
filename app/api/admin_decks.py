"""Authenticated FastAPI routes for demo deck generation and activation.

The router validates frozen contract models, delegates all behavior to the
injectable deck-admin service, and schedules expensive generation only after a
generating row exists. It never accepts or returns credentials or inline image
payloads.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.deck_admin.deps import get_deck_admin_service, require_deck_admin_key
from app.deck_admin.service import DeckAdminError, DeckAdminService
from contracts.api_types import (
    AdminDeckDetail,
    AdminDeckGenerateRequest,
    AdminDeckListResponse,
    AdminDeckOperationResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/decks",
    tags=["deck-admin"],
    dependencies=[Depends(require_deck_admin_key)],
)
ServiceDependency = Annotated[DeckAdminService, Depends(get_deck_admin_service)]


def _raise_admin_error(exc: DeckAdminError) -> None:
    """Translate a safe service error to FastAPI's HTTP error.

    Args:
        exc: Expected deck-admin domain error.

    Raises:
        HTTPException: Always, preserving the safe status and detail.
    """
    logger.info(
        "_raise_admin_error called status_code=%s detail_chars=%s",
        exc.status_code,
        len(exc.detail),
    )
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post(
    "",
    response_model=AdminDeckOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_deck(
    payload: AdminDeckGenerateRequest,
    background_tasks: BackgroundTasks,
    service: ServiceDependency,
) -> AdminDeckOperationResponse:
    """Create a generating deck and schedule expensive work after response.

    Args:
        payload: Validated operator region and concepts.
        background_tasks: FastAPI response-lifecycle task scheduler.
        service: Injected deck administration service.

    Returns:
        A 202 operation response identifying the generating deck.
    """
    logger.info(
        "generate_deck called region_tag=%s concept_count=%s",
        payload.region_tag,
        len(payload.concepts),
    )
    response = await service.start_generation(payload)
    background_tasks.add_task(service.run_generation, response.deck_id, payload)
    logger.info("generate_deck scheduled deck_id=%s", response.deck_id)
    return response


@router.get("", response_model=AdminDeckListResponse)
async def list_decks(service: ServiceDependency) -> AdminDeckListResponse:
    """List newest decks for the operator review screen.

    Args:
        service: Injected deck administration service.

    Returns:
        Bounded newest-first deck summaries.
    """
    logger.info("list_decks route called")
    response = await service.list_decks()
    logger.info("list_decks route completed deck_count=%s", len(response.decks))
    return response


@router.get("/{deck_id}", response_model=AdminDeckDetail)
async def review_deck(
    deck_id: UUID,
    service: ServiceDependency,
) -> AdminDeckDetail:
    """Return one deck's operator concepts and card review metadata.

    Args:
        deck_id: Deck UUID from the path.
        service: Injected deck administration service.

    Returns:
        Deck detail containing same-origin image URLs, never inline images.
    """
    logger.info("review_deck route called deck_id=%s", deck_id)
    try:
        response = await service.review_deck(deck_id)
    except DeckAdminError as exc:
        _raise_admin_error(exc)
    logger.info("review_deck route completed deck_id=%s", deck_id)
    return response


@router.post(
    "/{deck_id}/activate",
    response_model=AdminDeckOperationResponse,
)
async def activate_deck(
    deck_id: UUID,
    service: ServiceDependency,
) -> AdminDeckOperationResponse:
    """Atomically promote a ready deck to be the sole live deck.

    Args:
        deck_id: Ready or already-live deck UUID.
        service: Injected deck administration service.

    Returns:
        Live operation response. Repeating activation for a live deck is safe.
    """
    logger.info("activate_deck route called deck_id=%s", deck_id)
    try:
        response = await service.activate(deck_id)
    except DeckAdminError as exc:
        _raise_admin_error(exc)
    logger.info("activate_deck route completed deck_id=%s", deck_id)
    return response
