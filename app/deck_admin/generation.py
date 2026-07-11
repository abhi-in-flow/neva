"""Late-bound adapter from the admin service to the deck generation pipeline.

Deck generation is imported only when background work runs. This keeps route
imports stable while ``deckgen`` evolves and gives tests one narrow callable to
replace without invoking Gemini, Postgres publication, or image writes.
"""

from __future__ import annotations

import logging
from importlib import import_module
from typing import Any, Mapping, Protocol
from uuid import UUID

logger = logging.getLogger(__name__)


class DeckGenerationGateway(Protocol):
    """Background generation operation required by the admin service."""

    async def generate(
        self,
        *,
        region_tag: str,
        concepts: list[Mapping[str, str]],
        deck_id: UUID,
    ) -> None:
        """Generate cards into an existing deck and leave it ready."""


class DeckgenGateway:
    """Adapter for the operator conversion and deck pipeline entrypoints."""

    async def generate(
        self,
        *,
        region_tag: str,
        concepts: list[Mapping[str, str]],
        deck_id: UUID,
    ) -> None:
        """Convert operator concepts and build into an existing ready deck.

        Args:
            region_tag: Validated region tag.
            concepts: Validated operator concept dictionaries.
            deck_id: Existing generating deck to populate.

        Side effects:
            Invokes the deck generator, which may call GenAI, write images, and
            update Postgres. No image or credential payload is logged here.
        """
        logger.info(
            "DeckgenGateway.generate called deck_id=%s region_tag=%s "
            "concept_count=%s",
            deck_id,
            region_tag,
            len(concepts),
        )
        concepts_module = import_module("deckgen.concepts")
        pipeline_module = import_module("deckgen.pipeline")
        converter: Any = getattr(concepts_module, "concepts_from_operator")
        builder: Any = getattr(pipeline_module, "build_deck")
        converted = converter(concepts)
        await builder(
            region=region_tag,
            concepts=converted,
            deck_id=deck_id,
            final_status="ready",
        )
        logger.info("DeckgenGateway.generate completed deck_id=%s", deck_id)
