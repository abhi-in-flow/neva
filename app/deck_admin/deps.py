"""FastAPI dependencies for deck-admin authentication and service injection.

Authentication uses the demo-only shared key from application settings and
constant-time comparison. The key and supplied header are never logged,
fingerprinted, persisted, or returned.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import Header, HTTPException, Request

from app.config import get_settings
from app.deck_admin.generation import DeckgenGateway
from app.deck_admin.repository import PostgresDeckAdminRepository
from app.deck_admin.service import DeckAdminService

logger = logging.getLogger(__name__)


async def require_deck_admin_key(
    x_deck_admin_key: Annotated[
        str | None,
        Header(alias="X-Deck-Admin-Key"),
    ] = None,
) -> None:
    """Authenticate a deck-control request with constant-time comparison.

    Args:
        x_deck_admin_key: Shared key supplied in ``X-Deck-Admin-Key``.

    Raises:
        HTTPException: 503 when server key configuration is absent; 401 when
            the caller header is absent or incorrect.
    """
    settings = get_settings()
    configured = bool(settings.deck_admin_api_key)
    logger.info(
        "require_deck_admin_key called configured=%s header_present=%s",
        configured,
        x_deck_admin_key is not None,
    )
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="Deck administration is not configured",
        )
    supplied = (x_deck_admin_key or "").encode("utf-8")
    expected = settings.deck_admin_api_key.encode("utf-8")
    if not x_deck_admin_key or not hmac.compare_digest(supplied, expected):
        logger.info("require_deck_admin_key rejected")
        raise HTTPException(status_code=401, detail="Invalid deck admin key")
    logger.info("require_deck_admin_key completed authenticated=True")


def get_deck_admin_service(request: Request) -> DeckAdminService:
    """Resolve or create the process deck-admin service.

    Args:
        request: Incoming request exposing ``app.state.pool``.

    Returns:
        Shared injectable ``DeckAdminService``.

    Side effects:
        Caches a Postgres-backed service on ``app.state`` on first use.
    """
    logger.info("get_deck_admin_service called")
    existing = getattr(request.app.state, "deck_admin_service", None)
    if existing is not None:
        return existing
    settings = get_settings()
    service = DeckAdminService(
        PostgresDeckAdminRepository(request.app.state.pool),
        DeckgenGateway(),
        data_dir=settings.data_dir,
    )
    request.app.state.deck_admin_service = service
    logger.info("get_deck_admin_service completed created=True")
    return service
