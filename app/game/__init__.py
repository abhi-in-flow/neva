"""Game-core domain package for Dialect Data Factory.

Owns matchmaking, turn progression, audio acceptance, scoring, state
composition, leaderboard aggregation, and job enqueue helpers. This package is
the server-side source of truth for player-visible phases; API routers in
``app.api`` only translate HTTP into these services. Persistence is abstracted
behind store protocols so tests can run against an in-memory backend without
touching live Postgres or Gemini.
"""

from app.game.service import GameService

__all__ = ["GameService"]
