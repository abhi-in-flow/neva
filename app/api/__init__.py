"""HTTP API package for Dialect Data Factory game-core routes.

Routers translate bearer-authenticated requests into ``GameService`` calls and
return contract models from ``contracts.api_types``. Game rules stay in
``app.game``; this package must not invent phases, scores, or visibility.
"""

from app.api.routers import include_game_routers

__all__ = ["include_game_routers"]
