"""FastAPI dependency wiring for the tune administration service.

The dependency constructs one process-local service over the configured
filesystem repository and caches it on ``app.state``. Authentication remains
the shared ``X-Deck-Admin-Key`` dependency registered by the API router.
"""

from __future__ import annotations

import logging

from fastapi import Request

from app.tune_admin.config import get_tune_admin_config
from app.tune_admin.repository import TuneAdminRepository
from app.tune_admin.service import TuneAdminService

logger = logging.getLogger(__name__)


def get_tune_admin_service(request: Request) -> TuneAdminService:
    """Resolve or create the process tune-admin service.

    Args:
        request: Incoming request exposing the FastAPI application state.

    Returns:
        Shared ``TuneAdminService`` instance.

    Side effects:
        Caches a filesystem-backed service on ``app.state`` on first use.
    """
    logger.info("get_tune_admin_service called")
    existing = getattr(request.app.state, "tune_admin_service", None)
    if existing is not None:
        return existing
    config = get_tune_admin_config()
    service = TuneAdminService(TuneAdminRepository(config), config)
    request.app.state.tune_admin_service = service
    logger.info("get_tune_admin_service completed created=True")
    return service
