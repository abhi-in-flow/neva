"""Router inclusion helper for orchestrator-owned ``app.main``.

Game-core must not edit ``app/main.py``. The Wave 1 orchestrator wires routes
by calling ``include_game_routers(app)`` after the FastAPI app is constructed.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api import admin_decks, join, leaderboard, metrics, pair, state, turn

logger = logging.getLogger(__name__)


def include_game_routers(app: FastAPI) -> None:
    """Attach all game-core API routers to the FastAPI application.

    Args:
        app: The process FastAPI app from ``app.main``.

    Returns:
        None.

    Side effects:
        Registers join, pair, state, turn, leaderboard, metrics, and protected
        deck-administration routes.
        Does not modify lifespan, static mounts, or health probes.
    """
    logger.info("include_game_routers called")
    app.include_router(join.router)
    app.include_router(pair.router)
    app.include_router(state.router)
    app.include_router(turn.router)
    app.include_router(leaderboard.router)
    app.include_router(metrics.router)
    app.include_router(admin_decks.router)
    logger.info("include_game_routers completed router_count=7")
