"""End-to-end regional picture-deck generation pipeline.

Orchestrates curated or explicit operator concepts, NB2 image generation with
bounded verification retries and per-concept locale overrides, batched label
translation, same-deck decoys, metrics, and atomic ready/live publication.

GenAI I/O goes through ``DeckGenAIClient`` only. Dry-run uses fakes and an
in-memory publisher so no API, DB, or runtime-data mutation occurs.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from deckgen.client import DeckGenAIClient, build_client
from deckgen.concepts import Concept, select_concepts
from deckgen.config import (
    DECK_FINAL_STATUSES,
    DECK_STATUS_LIVE,
    DEFAULT_CARD_COUNT,
    IMAGE_MIME_TYPE,
    IMAGE_MODEL,
    MAX_IMAGE_RETRIES,
    N_DECOYS,
    STRENGTHENED_REGION_SUFFIX,
    TARGET_LANGUAGES,
    TRANSLATE_MODEL,
    VERIFY_MODEL,
    VERIFY_THINKING_LEVEL,
    DECOY_MODEL,
    get_settings,
    resolve_region_context,
)
from deckgen.metrics import DeckMetrics
from deckgen.prompts import (
    DECOY_RESPONSE_SCHEMA,
    DECOY_SELECTION_PROMPT,
    NB2_IMAGE_PROMPT,
    TRANSLATE_LABELS_PROMPT,
    TRANSLATE_RESPONSE_SCHEMA,
    VERIFY_IMAGE_PROMPT,
    VERIFY_RESPONSE_SCHEMA,
)
from deckgen.publish import (
    CardRecord,
    DeckPublisher,
    InMemoryPublisher,
    PostgresPublisher,
    PublishResult,
)

logger = logging.getLogger(__name__)


@dataclass
class BuiltCard:
    """In-memory card after generation/verification, before decoy UUID mapping.

    Attributes:
        card_id: Pre-assigned card UUID.
        concept: Source curated concept.
        image_bytes: Accepted PNG bytes.
        labels: Multilingual labels (post-translation when available).
        decoy_concept_ids: Decoy concept ids from the same deck pool.
    """

    card_id: uuid.UUID
    concept: Concept
    image_bytes: bytes
    labels: dict[str, str]
    decoy_concept_ids: list[str] = field(default_factory=list)


@dataclass
class DeckBuildResult:
    """Full pipeline outcome including metrics and publish status.

    Attributes:
        region: Region tag.
        cards: Built cards with decoy concept ids resolved.
        metrics: Throughput/cost metrics.
        publish: Publication result (or None if publish was skipped on error).
        dry_run: Whether the run was dry-run.
    """

    region: str
    cards: list[BuiltCard]
    metrics: DeckMetrics
    publish: PublishResult | None
    dry_run: bool


def verification_accepted(result: dict[str, Any]) -> bool:
    """Return True when a verification JSON payload is an accept.

    Accept rule (prompt pack): ``verdict == "pass"`` and ``depicts_label`` and
    not ``has_text``. ``cultural_ok=false`` alone is treated as reject so the
    pipeline regenerates with a strengthened region clause.

    Args:
        result: Parsed verification JSON.

    Returns:
        Whether the image should be kept.
    """
    logger.info(
        "verification_accepted called verdict=%s depicts_label=%s has_text=%s cultural_ok=%s",
        result.get("verdict"),
        result.get("depicts_label"),
        result.get("has_text"),
        result.get("cultural_ok"),
    )
    ok = (
        result.get("verdict") == "pass"
        and bool(result.get("depicts_label"))
        and not bool(result.get("has_text"))
        and bool(result.get("cultural_ok", True))
    )
    logger.info("verification_accepted result=%s", ok)
    return ok


def needs_region_emphasis(result: dict[str, Any]) -> bool:
    """Detect verification failures that should strengthen the region clause.

    Args:
        result: Parsed verification JSON.

    Returns:
        True when ``cultural_ok`` is explicitly false.
    """
    logger.info(
        "needs_region_emphasis called cultural_ok=%s",
        result.get("cultural_ok"),
    )
    return result.get("cultural_ok") is False


def format_image_prompt(
    concept: Concept,
    region_context: str,
    *,
    strengthen_region: bool = False,
) -> str:
    """Format the NB2 image prompt for one concept.

    Args:
        concept: Curated concept.
        region_context: Region context phrase.
        strengthen_region: Append the strengthened region emphasis clause.

    Returns:
        Fully substituted prompt string.
    """
    emphasis = (
        STRENGTHENED_REGION_SUFFIX.format(region_context=region_context)
        if strengthen_region
        else ""
    )
    prompt = NB2_IMAGE_PROMPT.format(
        concept_phrase=concept.concept_phrase,
        concept_noun=concept.concept_noun,
        region_context=region_context,
        region_emphasis=emphasis,
    )
    logger.info(
        "format_image_prompt concept_id=%s region_context=%s strengthen_region=%s prompt_chars=%s",
        concept.id,
        region_context,
        strengthen_region,
        len(prompt),
    )
    return prompt


async def generate_verified_image(
    client: DeckGenAIClient,
    concept: Concept,
    region_context: str,
    metrics: DeckMetrics,
    *,
    max_retries: int = MAX_IMAGE_RETRIES,
) -> bytes:
    """Generate an image and verify it, regenerating up to ``max_retries``.

    Args:
        client: GenAI client (fake or shared adapter).
        concept: Concept to depict.
        region_context: Cultural region context.
        metrics: Metrics accumulator.
        max_retries: Retries after the first attempt.

    Returns:
        Accepted image bytes.

    Raises:
        RuntimeError: If all attempts fail verification.
    """
    logger.info(
        "generate_verified_image called concept_id=%s max_retries=%s",
        concept.id,
        max_retries,
    )
    strengthen = False
    last_reason = "unknown"
    attempts = max_retries + 1
    for attempt in range(attempts):
        prompt = format_image_prompt(concept, region_context, strengthen_region=strengthen)
        logger.info(
            "GenAI request generate_image model=%s operation=generate_card_image "
            "attempt=%s concept_id=%s prompt_chars=%s",
            IMAGE_MODEL,
            attempt,
            concept.id,
            len(prompt),
        )
        image = await client.generate_image(
            model=IMAGE_MODEL,
            prompt=prompt,
            operation="generate_card_image",
        )
        metrics.record_image_attempt()
        logger.info(
            "GenAI response generate_image model=%s operation=generate_card_image "
            "byte_length=%s sha256_hex=%s mime_type=%s",
            IMAGE_MODEL,
            image.byte_length,
            image.sha256_hex,
            image.mime_type,
        )

        verify_prompt = VERIFY_IMAGE_PROMPT.format(
            label_en=concept.labels["en"],
            region_context=region_context,
        )
        logger.info(
            "GenAI request generate_json model=%s operation=verify_image "
            "thinking_level=%s prompt_chars=%s image_byte_length=%s",
            VERIFY_MODEL,
            VERIFY_THINKING_LEVEL,
            len(verify_prompt),
            image.byte_length,
        )
        verdict = await client.generate_json(
            model=VERIFY_MODEL,
            prompt=verify_prompt,
            operation="verify_image",
            response_schema=VERIFY_RESPONSE_SCHEMA,
            thinking_level=VERIFY_THINKING_LEVEL,
            image_bytes=image.data,
            image_mime_type=image.mime_type or IMAGE_MIME_TYPE,
        )
        metrics.record_flash_call()
        if not isinstance(verdict, dict):
            raise TypeError(f"verify_image expected dict, got {type(verdict)}")
        logger.info(
            "GenAI response generate_json model=%s operation=verify_image "
            "verdict=%s reason=%s depicts_label=%s has_text=%s cultural_ok=%s",
            VERIFY_MODEL,
            verdict.get("verdict"),
            verdict.get("reason"),
            verdict.get("depicts_label"),
            verdict.get("has_text"),
            verdict.get("cultural_ok"),
        )
        if verification_accepted(verdict):
            metrics.record_accept()
            logger.info(
                "generate_verified_image accepted concept_id=%s attempt=%s",
                concept.id,
                attempt,
            )
            return image.data

        metrics.record_reject()
        last_reason = str(verdict.get("reason", "rejected"))
        # Any reject strengthens regional + humor guidance on the next attempt.
        strengthen = True
        if needs_region_emphasis(verdict):
            logger.info(
                "generate_verified_image cultural_ok failed concept_id=%s "
                "attempt=%s",
                concept.id,
                attempt,
            )
        logger.info(
            "generate_verified_image rejected concept_id=%s attempt=%s "
            "reason=%s strengthen_next=%s",
            concept.id,
            attempt,
            last_reason,
            strengthen,
        )

    raise RuntimeError(
        f"Image verification failed for concept {concept.id!r} after "
        f"{attempts} attempts: {last_reason}"
    )


async def translate_labels_batch(
    client: DeckGenAIClient,
    concepts: list[Concept],
    metrics: DeckMetrics,
    *,
    languages: tuple[str, ...] = TARGET_LANGUAGES,
) -> dict[str, dict[str, str]]:
    """Batch-translate English labels into target languages.

    Args:
        client: GenAI client.
        concepts: Concepts in the deck.
        metrics: Metrics accumulator.
        languages: Target language codes.

    Returns:
        Map of concept id → full label dict (always includes ``en``).
    """
    payload = [{"id": c.id, "en": c.labels["en"]} for c in concepts]
    prompt = TRANSLATE_LABELS_PROMPT.format(
        json_list=json.dumps(payload, ensure_ascii=False),
        lang_list=json.dumps(list(languages)),
    )
    logger.info(
        "GenAI request generate_json model=%s operation=translate_labels "
        "concept_count=%s languages=%s prompt_chars=%s",
        TRANSLATE_MODEL,
        len(concepts),
        list(languages),
        len(prompt),
    )
    raw = await client.generate_json(
        model=TRANSLATE_MODEL,
        prompt=prompt,
        operation="translate_labels",
        response_schema=TRANSLATE_RESPONSE_SCHEMA,
        thinking_level=VERIFY_THINKING_LEVEL,
    )
    metrics.record_flash_call()
    logger.info(
        "GenAI response generate_json model=%s operation=translate_labels row_count=%s",
        TRANSLATE_MODEL,
        len(raw) if isinstance(raw, list) else 0,
    )
    by_id: dict[str, dict[str, str]] = {c.id: dict(c.labels) for c in concepts}
    if not isinstance(raw, list):
        raise TypeError("translate_labels expected a JSON array")
    for row in raw:
        cid = str(row["id"])
        labels = {str(k): str(v) for k, v in dict(row["labels"]).items()}
        if "en" not in labels:
            labels["en"] = by_id[cid]["en"]
        by_id[cid] = labels
    return by_id


async def select_decoys_batch(
    client: DeckGenAIClient,
    cards: list[BuiltCard],
    metrics: DeckMetrics,
    *,
    n_decoys: int = N_DECOYS,
) -> dict[str, list[str]]:
    """Ask Gemini to pick decoys from the same-deck concept pool only.

    Args:
        client: GenAI client.
        cards: Built cards (card_id temporarily used as the selection key;
            concept ids are the only legal decoy values).
        metrics: Metrics accumulator.
        n_decoys: Number of decoys per card.

    Returns:
        Map of card UUID string → list of decoy concept ids.

    Raises:
        ValueError: If the model returns ids outside the provided pool or
            self-decoys / wrong counts.
    """
    pool = [{"concept_id": c.concept.id, "label_en": c.labels["en"]} for c in cards]
    targets = [
        {
            "card_id": str(c.card_id),
            "concept_id": c.concept.id,
            "label_en": c.labels["en"],
        }
        for c in cards
    ]
    block = {"targets": targets, "pool": pool}
    prompt = DECOY_SELECTION_PROMPT.format(
        n_decoys=n_decoys,
        json_block=json.dumps(block, ensure_ascii=False),
    )
    logger.info(
        "GenAI request generate_json model=%s operation=select_decoys "
        "card_count=%s n_decoys=%s prompt_chars=%s",
        DECOY_MODEL,
        len(cards),
        n_decoys,
        len(prompt),
    )
    raw = await client.generate_json(
        model=DECOY_MODEL,
        prompt=prompt,
        operation="select_decoys",
        response_schema=DECOY_RESPONSE_SCHEMA,
        thinking_level=VERIFY_THINKING_LEVEL,
    )
    metrics.record_flash_call()
    logger.info(
        "GenAI response generate_json model=%s operation=select_decoys row_count=%s",
        DECOY_MODEL,
        len(raw) if isinstance(raw, list) else 0,
    )
    if not isinstance(raw, list):
        raise TypeError("select_decoys expected a JSON array")

    allowed = {c.concept.id for c in cards}
    card_ids = {str(c.card_id) for c in cards}
    concept_of_card = {str(c.card_id): c.concept.id for c in cards}
    mapping: dict[str, list[str]] = {}
    for row in raw:
        card_id = str(row["card_id"])
        if card_id not in card_ids:
            raise ValueError(f"decoy response references unknown card_id={card_id}")
        decoys = [str(x) for x in row["decoy_concept_ids"]]
        own = concept_of_card[card_id]
        if len(decoys) != n_decoys:
            raise ValueError(f"card {card_id} expected {n_decoys} decoys, got {len(decoys)}")
        if own in decoys:
            raise ValueError(f"card {card_id} decoys include its own concept {own}")
        unknown = [d for d in decoys if d not in allowed]
        if unknown:
            raise ValueError(f"card {card_id} decoys outside pool: {unknown}")
        if len(set(decoys)) != len(decoys):
            raise ValueError(f"card {card_id} has duplicate decoy concept ids")
        mapping[card_id] = decoys
    missing = card_ids - set(mapping)
    if missing:
        raise ValueError(f"decoy response missing cards: {sorted(missing)}")
    return mapping


def map_decoy_concepts_to_card_uuids(
    cards: list[BuiltCard],
    decoy_concepts_by_card: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Resolve decoy concept ids to same-deck card UUID strings.

    Args:
        cards: Built cards in the deck.
        decoy_concepts_by_card: card UUID string → decoy concept ids.

    Returns:
        card UUID string → decoy card UUID strings (for ``cards.decoys``).

    Raises:
        KeyError: If a decoy concept is not present in the deck.
    """
    concept_to_card = {c.concept.id: str(c.card_id) for c in cards}
    logger.info(
        "map_decoy_concepts_to_card_uuids called card_count=%s",
        len(cards),
    )
    out: dict[str, list[str]] = {}
    for card_id, concept_ids in decoy_concepts_by_card.items():
        out[card_id] = [concept_to_card[cid] for cid in concept_ids]
    return out


async def build_deck(
    *,
    region: str,
    cards: int | None = None,
    concepts: list[Concept] | None = None,
    dry_run: bool = False,
    client: DeckGenAIClient | None = None,
    publisher: DeckPublisher | None = None,
    seed: int | None = None,
    deck_id: uuid.UUID | None = None,
    final_status: str = DECK_STATUS_LIVE,
) -> DeckBuildResult:
    """Run the full deck pipeline and publish (or dry-run publish).

    Args:
        region: Region tag (``--region``).
        cards: Number of cards to generate. Defaults to the configured count
            for curated selection, or derives from explicit ``concepts``.
        concepts: Optional ordered concept list. When supplied, random curated
            selection is bypassed.
        dry_run: Use fake GenAI + in-memory publisher; no API/DB/data writes.
        client: Optional injected client (tests).
        publisher: Optional injected publisher (tests).
        seed: Optional concept-selection seed.
        deck_id: Optional pre-created ``generating`` deck UUID.
        final_status: Successful publication status, ``ready`` or ``live``.

    Returns:
        ``DeckBuildResult`` with metrics and publish outcome.

    Side effects:
        Live mode writes images under ``DATA_DIR`` and inserts Postgres rows.
        Dry-run has no external side effects.
    """
    logger.info(
        "build_deck called region=%s cards=%s explicit_concept_count=%s "
        "dry_run=%s seed=%s deck_id=%s final_status=%s",
        region,
        cards,
        len(concepts) if concepts is not None else None,
        dry_run,
        seed,
        deck_id,
        final_status,
    )
    if final_status not in DECK_FINAL_STATUSES:
        raise ValueError(f"final_status must be one of {DECK_FINAL_STATUSES}")
    region_key = region.strip().lower()
    region_context = resolve_region_context(region_key)
    metrics = DeckMetrics()

    genai = client or build_client(dry_run=dry_run)
    if publisher is not None:
        pub = publisher
    elif dry_run:
        pub = InMemoryPublisher()
    else:
        settings = get_settings()
        pub = PostgresPublisher(
            database_url=settings.database_url,
            data_dir=settings.data_dir,
        )

    try:
        if concepts is None:
            resolved_card_count = cards if cards is not None else DEFAULT_CARD_COUNT
            selected_concepts = select_concepts(resolved_card_count, seed=seed)
        else:
            selected_concepts = list(concepts)
            resolved_card_count = len(selected_concepts)
            if cards is not None and cards != resolved_card_count:
                raise ValueError(
                    f"cards={cards} does not match explicit concept count {resolved_card_count}"
                )
            concept_ids = [concept.id for concept in selected_concepts]
            if len(set(concept_ids)) != len(concept_ids):
                raise ValueError("explicit concepts contain duplicate ids")

        translated = await translate_labels_batch(genai, selected_concepts, metrics)

        built: list[BuiltCard] = []
        for concept in selected_concepts:
            concept_region_context = concept.locale or region_context
            image_bytes = await generate_verified_image(
                genai, concept, concept_region_context, metrics
            )
            built.append(
                BuiltCard(
                    card_id=uuid.uuid4(),
                    concept=concept,
                    image_bytes=image_bytes,
                    labels=translated[concept.id],
                )
            )

        decoy_concepts = await select_decoys_batch(genai, built, metrics)
        decoy_uuids = map_decoy_concepts_to_card_uuids(built, decoy_concepts)
        for card in built:
            card.decoy_concept_ids = decoy_concepts[str(card.card_id)]

        records = [
            CardRecord(
                card_id=card.card_id,
                concept_id=card.concept.id,
                image_bytes=card.image_bytes,
                label_common=card.labels,
                decoy_card_ids=decoy_uuids[str(card.card_id)],
                verified=True,
            )
            for card in built
        ]
        metrics.finish()
        metrics_payload = metrics.as_dict()
        generation_input = build_generation_input(
            region=region_key,
            concepts=selected_concepts,
            seed=seed,
            operator_supplied=concepts is not None,
        )
        publish_result = await pub.publish(
            region_tag=region_key,
            cards=records,
            deck_id=deck_id,
            final_status=final_status,
            generation_input=generation_input,
            generation_metrics=metrics_payload,
        )
    except Exception as exc:
        if metrics.finished_at is None:
            metrics.finish()
        if deck_id is not None:
            try:
                await pub.mark_failed(
                    deck_id=deck_id,
                    reason=f"generation failed: {type(exc).__name__}",
                )
            except Exception:
                logger.exception(
                    "build_deck could not mark provided deck failed deck_id=%s",
                    deck_id,
                )
        raise

    logger.info("build_deck metrics %s", metrics_payload)
    return DeckBuildResult(
        region=region_key,
        cards=built,
        metrics=metrics,
        publish=publish_result,
        dry_run=dry_run,
    )


def build_generation_input(
    *,
    region: str,
    concepts: list[Concept],
    seed: int | None,
    operator_supplied: bool,
) -> dict[str, Any]:
    """Create the safe, reproducible generation input stored with a deck.

    Args:
        region: Normalized deck region tag.
        concepts: Ordered concepts actually used by generation.
        seed: Optional curated selection seed.
        operator_supplied: Whether concepts came from an operator payload.

    Returns:
        JSON-compatible input containing only known non-secret deck fields.
    """
    logger.info(
        "build_generation_input called region=%s concept_count=%s operator_supplied=%s seed=%s",
        region,
        len(concepts),
        operator_supplied,
        seed,
    )
    return {
        "region": region,
        "card_count": len(concepts),
        "seed": seed,
        "source": "operator" if operator_supplied else "curated",
        "concepts": [
            {
                "concept_id": concept.id,
                "label_en": concept.labels["en"],
                "locale": concept.locale,
                "cultural_hint": concept.cultural_hint,
            }
            for concept in concepts
        ],
    }


def build_deck_sync(**kwargs: Any) -> DeckBuildResult:
    """Synchronous wrapper around ``build_deck`` for the CLI.

    Args:
        **kwargs: Forwarded to ``build_deck``.

    Returns:
        The async pipeline result.
    """
    import asyncio

    logger.info("build_deck_sync called keys=%s", sorted(kwargs))
    return asyncio.run(build_deck(**kwargs))
