"""Dialect Data Factory regional picture-deck engine.

Standalone CLI package that generates culturally grounded Indian picture
cards with Nano Banana 2 Lite, verifies image-label consistency with Gemini,
batches label translation and decoy selection, and publishes complete decks
atomically to Postgres plus ``data/decks/``.

Architectural boundary:
    - Owns all deck-generation prompts, curated concepts, pipeline logic,
      metrics, and dry-run fakes under ``deckgen/``.
    - Reads model identifiers from ``app.models`` and GenAI I/O through a
      deck-owned protocol adapted to ``app.gemini_client`` (orchestrator-owned).
    - Does not implement game rules, gauntlet cleaning, or frontend logic.
    - Dry-run mode never calls external APIs, mutates the database, or writes
      runtime data under ``DATA_DIR``.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
