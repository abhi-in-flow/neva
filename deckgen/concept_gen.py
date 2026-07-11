"""Gemini Flash invention of operator concepts from a one-line theme.

Primary admin prompt-to-deck path: given a region tag and short theme, ask
Gemini Flash for a strict ``{concepts:[...]}`` payload, then validate it through
the same operator concept conversion used by the JSON fallback path. Raw concept
JSON is never returned to the browser during generation; callers persist it only
on the generating deck row for later ready-state review.

Architectural boundary: GenAI I/O goes through ``DeckGenAIClient``. Model IDs
and retry budgets come from ``deckgen.config``; prompt text lives in
``deckgen.prompts``.
"""

from __future__ import annotations

import logging
from typing import Any

from contracts.api_types import AdminConceptInput

from deckgen.client import DeckGenAIClient
from deckgen.concepts import Concept, concepts_from_operator
from deckgen.config import (
    CONCEPT_MODEL,
    MAX_CONCEPT_GEN_RETRIES,
    PROMPT_MAX_CARD_COUNT,
    PROMPT_MIN_CARD_COUNT,
    VERIFY_THINKING_LEVEL,
    resolve_region_context,
)
from deckgen.metrics import DeckMetrics
from deckgen.prompts import (
    CONCEPT_FROM_PROMPT_PROMPT,
    CONCEPT_FROM_PROMPT_RESPONSE_SCHEMA,
)

logger = logging.getLogger(__name__)


def _operator_rows_from_payload(
    payload: Any,
    *,
    expected_count: int,
) -> list[dict[str, str]]:
    """Validate a Gemini invent-concepts payload into operator row dicts.

    Args:
        payload: Parsed JSON from the invent_concepts operation.
        expected_count: Exact number of concepts required.

    Returns:
        Ordered operator concept dictionaries ready for conversion.

    Raises:
        TypeError: If the top-level shape is wrong.
        ValueError: If count, uniqueness, or field validation fails.
    """
    logger.info(
        "_operator_rows_from_payload called expected_count=%s payload_type=%s",
        expected_count,
        type(payload).__name__,
    )
    if not isinstance(payload, dict):
        raise TypeError("invent_concepts expected a JSON object")
    raw_concepts = payload.get("concepts")
    if not isinstance(raw_concepts, list):
        raise TypeError("invent_concepts.concepts must be an array")
    if len(raw_concepts) != expected_count:
        raise ValueError(
            f"invent_concepts returned {len(raw_concepts)} concepts, "
            f"expected {expected_count}"
        )
    concepts = [AdminConceptInput.model_validate(row) for row in raw_concepts]
    concept_ids = [concept.concept_id for concept in concepts]
    if len(concept_ids) != len(set(concept_ids)):
        raise ValueError("invent_concepts returned duplicate concept_id values")
    rows = [concept.model_dump(mode="json") for concept in concepts]
    logger.info(
        "_operator_rows_from_payload completed concept_ids=%s",
        [row["concept_id"] for row in rows],
    )
    return rows


async def invent_concepts_from_prompt(
    client: DeckGenAIClient,
    *,
    region_tag: str,
    prompt: str,
    card_count: int,
    max_retries: int = MAX_CONCEPT_GEN_RETRIES,
    metrics: DeckMetrics | None = None,
) -> list[Concept]:
    """Ask Gemini Flash to invent validated operator concepts for a theme.

    Args:
        client: Deck GenAI client (fake or shared adapter).
        region_tag: Normalized or legacy region slug.
        prompt: One-line operator theme (already stripped by the contract).
        card_count: Exact concept count (must be within prompt min/max).
        max_retries: Retries after the first failed invent/validate attempt.
        metrics: Optional deck metrics receiving one Flash-call increment per
            concept-generation attempt.

    Returns:
        Validated ``Concept`` instances ready for progressive image generation.

    Raises:
        ValueError: If ``card_count`` is out of range or the region is unknown.
        RuntimeError: If all invent attempts fail validation.

    Side effects:
        Logs GenAI request/response metadata at INFO without secrets or full
        concept dumps beyond counts and ids.
    """
    logger.info(
        "invent_concepts_from_prompt called region_tag=%s prompt_chars=%s "
        "card_count=%s max_retries=%s",
        region_tag,
        len(prompt),
        card_count,
        max_retries,
    )
    if card_count < PROMPT_MIN_CARD_COUNT or card_count > PROMPT_MAX_CARD_COUNT:
        raise ValueError(
            f"card_count must be between {PROMPT_MIN_CARD_COUNT} and "
            f"{PROMPT_MAX_CARD_COUNT}, got {card_count}"
        )
    region_key = region_tag.strip().lower()
    region_context = resolve_region_context(region_key)
    themed_prompt = CONCEPT_FROM_PROMPT_PROMPT.format(
        card_count=card_count,
        region_tag=region_key,
        region_context=region_context,
        theme=prompt,
    )
    attempts = max_retries + 1
    last_error = "unknown"
    for attempt in range(attempts):
        logger.info(
            "GenAI request generate_json model=%s operation=invent_concepts "
            "attempt=%s prompt_chars=%s card_count=%s",
            CONCEPT_MODEL,
            attempt,
            len(themed_prompt),
            card_count,
        )
        raw = await client.generate_json(
            model=CONCEPT_MODEL,
            prompt=themed_prompt,
            operation="invent_concepts",
            response_schema=CONCEPT_FROM_PROMPT_RESPONSE_SCHEMA,
            thinking_level=VERIFY_THINKING_LEVEL,
        )
        if metrics is not None:
            metrics.record_flash_call()
        concept_count = (
            len(raw.get("concepts", [])) if isinstance(raw, dict) else 0
        )
        logger.info(
            "GenAI response generate_json model=%s operation=invent_concepts "
            "attempt=%s concept_count=%s",
            CONCEPT_MODEL,
            attempt,
            concept_count,
        )
        try:
            rows = _operator_rows_from_payload(raw, expected_count=card_count)
            concepts = concepts_from_operator(rows)
        except (TypeError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.info(
                "invent_concepts_from_prompt validation failed attempt=%s "
                "error=%s",
                attempt,
                type(exc).__name__,
            )
            continue
        logger.info(
            "invent_concepts_from_prompt completed concept_ids=%s",
            [concept.id for concept in concepts],
        )
        return concepts

    raise RuntimeError(
        f"Concept invention failed after {attempts} attempts: {last_error}"
    )
