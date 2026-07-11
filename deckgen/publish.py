"""Atomic and progressive deck publication to Postgres and local image storage.

Atomic mode writes all card images through a staging directory, inserts every
card in one transaction, then transitions the deck to ``ready`` or ``live``.
Progressive (admin prompt-to-deck) mode persists each verified card as soon as
it is ready while the deck remains ``generating``, updates generation metrics
for the review UI, then backfills decoys and finalizes status once the batch
completes. Callers may publish into an operator-pre-created ``generating`` row.

Dry-run and unit tests use ``InMemoryPublisher`` which records operations
without touching the database or ``DATA_DIR``.
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import asyncpg

from deckgen.config import (
    DECK_STATUS_FAILED,
    DECK_FINAL_STATUSES,
    DECK_STATUS_GENERATING,
    DECK_STATUS_LIVE,
    MAX_FAILURE_REASON_LENGTH,
    RELATIVE_DECKS_DIR,
    database_log_meta,
)

logger = logging.getLogger(__name__)


def image_file_extension(image_bytes: bytes) -> str:
    """Return a safe file extension for supported encoded image bytes.

    Args:
        image_bytes: Complete encoded image payload returned by Gemini.

    Returns:
        One of ``.png``, ``.jpg``, or ``.webp`` based on file signatures.

    Raises:
        ValueError: When the payload is empty or has an unsupported signature.
    """
    logger.info("image_file_extension called byte_length=%s", len(image_bytes))
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if (
        len(image_bytes) >= 12
        and image_bytes.startswith(b"RIFF")
        and image_bytes[8:12] == b"WEBP"
    ):
        return ".webp"
    raise ValueError("unsupported generated image encoding")


@dataclass
class CardRecord:
    """One verified card ready for publication.

    Attributes:
        card_id: UUID assigned before insert (also used in image filenames).
        concept_id: Curated concept id (for decoy mapping / logging).
        image_bytes: Encoded PNG, JPEG, or WebP bytes to write under the deck directory.
        label_common: Multilingual label map stored as JSONB.
        decoy_card_ids: Same-deck card UUID strings for ``cards.decoys``.
        verified: Always True for published cards.
    """

    card_id: uuid.UUID
    concept_id: str
    image_bytes: bytes
    label_common: dict[str, str]
    decoy_card_ids: list[str] = field(default_factory=list)
    verified: bool = True


@dataclass
class PublishResult:
    """Outcome of an atomic publish attempt.

    Attributes:
        deck_id: Deck UUID.
        status: Final successful deck status (``ready`` or ``live``).
        card_ids: Published card UUID strings.
        image_paths: Relative image paths stored in Postgres.
        dry_run: Whether this was a no-mutation publish.
    """

    deck_id: uuid.UUID
    status: str
    card_ids: list[str]
    image_paths: list[str]
    dry_run: bool


@runtime_checkable
class DeckPublisher(Protocol):
    """Publication backend used by the pipeline."""

    async def publish(
        self,
        *,
        region_tag: str,
        cards: list[CardRecord],
        deck_id: uuid.UUID | None = None,
        final_status: str = DECK_STATUS_LIVE,
        generation_input: Mapping[str, Any] | None = None,
        generation_metrics: Mapping[str, Any] | None = None,
    ) -> PublishResult:
        """Persist a complete deck atomically."""

    async def mark_failed(
        self,
        *,
        deck_id: uuid.UUID,
        reason: str,
    ) -> None:
        """Mark an existing generating deck failed without publishing cards."""

    async def update_generation_state(
        self,
        *,
        deck_id: uuid.UUID,
        generation_input: Mapping[str, Any] | None = None,
        generation_metrics: Mapping[str, Any] | None = None,
    ) -> None:
        """Update generation_input and/or metrics on a generating deck."""

    async def persist_card(
        self,
        *,
        deck_id: uuid.UUID,
        card: CardRecord,
        generation_metrics: Mapping[str, Any] | None = None,
    ) -> str:
        """Persist one verified card while the deck remains generating."""

    async def finalize_progressive(
        self,
        *,
        deck_id: uuid.UUID,
        decoys_by_card_id: Mapping[str, list[str]],
        generation_metrics: Mapping[str, Any] | None = None,
        final_status: str = DECK_STATUS_LIVE,
    ) -> PublishResult:
        """Backfill decoys and transition a progressive deck to ready/live."""


class InMemoryPublisher:
    """Test/dry-run publisher that never touches DB or runtime data dirs.

    Records each publish call. Can be configured to fail mid-flight to assert
    that callers do not treat a failed publish as live.
    """

    def __init__(self, *, fail: bool = False) -> None:
        """Create an in-memory publisher.

        Args:
            fail: When True, ``publish`` raises after recording intent.
        """
        logger.info("InMemoryPublisher.__init__ fail=%s", fail)
        self.fail = fail
        self.published: list[dict[str, Any]] = []
        self.live_decks: list[uuid.UUID] = []
        self.ready_decks: list[uuid.UUID] = []
        self.failed_decks: list[uuid.UUID] = []
        self.progressive_cards: dict[str, list[dict[str, Any]]] = {}
        self.generation_states: dict[str, dict[str, Any]] = {}
        self.deck_statuses: dict[str, str] = {}

    async def publish(
        self,
        *,
        region_tag: str,
        cards: list[CardRecord],
        deck_id: uuid.UUID | None = None,
        final_status: str = DECK_STATUS_LIVE,
        generation_input: Mapping[str, Any] | None = None,
        generation_metrics: Mapping[str, Any] | None = None,
    ) -> PublishResult:
        """Record a dry-run publish without filesystem or DB mutation.

        Args:
            region_tag: Region tag for the deck row.
            cards: Verified cards with decoy UUID lists.
            deck_id: Optional existing generating deck UUID.
            final_status: Successful terminal status.
            generation_input: Safe generation parameters.
            generation_metrics: Final throughput/cost counters.

        Returns:
            ``PublishResult`` with ``dry_run=True`` and the requested status,
            or raises if ``fail`` is set (no finalized deck recorded).

        Raises:
            RuntimeError: When configured to fail (simulates aborted txn).
        """
        if final_status not in DECK_FINAL_STATUSES:
            raise ValueError(f"final_status must be one of {DECK_FINAL_STATUSES}")
        resolved_deck_id = deck_id or uuid.uuid4()
        logger.info(
            "InMemoryPublisher.publish called deck_id=%s region_tag=%s "
            "card_count=%s final_status=%s provided_deck_id=%s fail=%s",
            resolved_deck_id,
            region_tag,
            len(cards),
            final_status,
            deck_id is not None,
            self.fail,
        )
        intent = {
            "deck_id": str(resolved_deck_id),
            "region_tag": region_tag,
            "card_ids": [str(c.card_id) for c in cards],
            "concept_ids": {str(c.card_id): c.concept_id for c in cards},
            "decoys": {str(c.card_id): list(c.decoy_card_ids) for c in cards},
            "final_status": final_status,
            "generation_input": dict(generation_input or {}),
            "generation_metrics": dict(generation_metrics or {}),
            "provided_deck_id": deck_id is not None,
        }
        self.published.append(intent)
        if self.fail:
            logger.info("InMemoryPublisher.publish aborting (configured fail)")
            raise RuntimeError("simulated publish failure")
        if final_status == DECK_STATUS_LIVE:
            self.live_decks.append(resolved_deck_id)
        else:
            self.ready_decks.append(resolved_deck_id)
        self.deck_statuses[str(resolved_deck_id)] = final_status
        paths = [
            f"{RELATIVE_DECKS_DIR}/{resolved_deck_id}/{c.card_id}"
            f"{image_file_extension(c.image_bytes)}"
            for c in cards
        ]
        result = PublishResult(
            deck_id=resolved_deck_id,
            status=final_status,
            card_ids=[str(c.card_id) for c in cards],
            image_paths=paths,
            dry_run=True,
        )
        logger.info(
            "InMemoryPublisher.publish completed deck_id=%s status=%s",
            resolved_deck_id,
            result.status,
        )
        return result

    async def mark_failed(self, *, deck_id: uuid.UUID, reason: str) -> None:
        """Record a failed pre-created deck without external mutation.

        Args:
            deck_id: Existing operator-created deck UUID.
            reason: Safe failure summary; stored only for test inspection.
        """
        logger.info(
            "InMemoryPublisher.mark_failed called deck_id=%s reason_chars=%s",
            deck_id,
            len(reason),
        )
        self.failed_decks.append(deck_id)
        self.deck_statuses[str(deck_id)] = DECK_STATUS_FAILED

    async def update_generation_state(
        self,
        *,
        deck_id: uuid.UUID,
        generation_input: Mapping[str, Any] | None = None,
        generation_metrics: Mapping[str, Any] | None = None,
    ) -> None:
        """Record progressive generation_input/metrics updates in memory.

        Args:
            deck_id: Generating deck UUID.
            generation_input: Optional replacement generation input.
            generation_metrics: Optional replacement metrics payload.
        """
        key = str(deck_id)
        logger.info(
            "InMemoryPublisher.update_generation_state called deck_id=%s "
            "has_input=%s has_metrics=%s",
            deck_id,
            generation_input is not None,
            generation_metrics is not None,
        )
        state = self.generation_states.setdefault(key, {})
        if generation_input is not None:
            state["generation_input"] = dict(generation_input)
        if generation_metrics is not None:
            state["generation_metrics"] = dict(generation_metrics)
        self.deck_statuses.setdefault(key, DECK_STATUS_GENERATING)

    async def persist_card(
        self,
        *,
        deck_id: uuid.UUID,
        card: CardRecord,
        generation_metrics: Mapping[str, Any] | None = None,
    ) -> str:
        """Record one progressive card without filesystem or DB mutation.

        Args:
            deck_id: Generating deck UUID.
            card: Verified card (decoys may be empty until finalize).
            generation_metrics: Optional progress metrics snapshot.

        Returns:
            Relative image path that would be stored in Postgres.
        """
        key = str(deck_id)
        logger.info(
            "InMemoryPublisher.persist_card called deck_id=%s card_id=%s "
            "concept_id=%s byte_length=%s",
            deck_id,
            card.card_id,
            card.concept_id,
            len(card.image_bytes),
        )
        cards = self.progressive_cards.setdefault(key, [])
        existing_concepts = {row["concept_id"] for row in cards}
        if card.concept_id in existing_concepts:
            path = next(
                str(row["image_path"])
                for row in cards
                if row["concept_id"] == card.concept_id
            )
            logger.info(
                "InMemoryPublisher.persist_card skipped duplicate concept_id=%s",
                card.concept_id,
            )
        else:
            path = (
                f"{RELATIVE_DECKS_DIR}/{deck_id}/{card.card_id}"
                f"{image_file_extension(card.image_bytes)}"
            )
            cards.append(
                {
                    "card_id": str(card.card_id),
                    "concept_id": card.concept_id,
                    "image_path": path,
                    "label_common": dict(card.label_common),
                    "decoy_card_ids": list(card.decoy_card_ids),
                    "verified": card.verified,
                }
            )
        if generation_metrics is not None:
            await self.update_generation_state(
                deck_id=deck_id,
                generation_metrics=generation_metrics,
            )
        self.deck_statuses[key] = DECK_STATUS_GENERATING
        return path

    async def finalize_progressive(
        self,
        *,
        deck_id: uuid.UUID,
        decoys_by_card_id: Mapping[str, list[str]],
        generation_metrics: Mapping[str, Any] | None = None,
        final_status: str = DECK_STATUS_LIVE,
    ) -> PublishResult:
        """Apply decoys and mark a progressive in-memory deck ready/live.

        Args:
            deck_id: Generating deck UUID with progressive cards.
            decoys_by_card_id: card UUID string → decoy card UUID strings.
            generation_metrics: Final metrics payload.
            final_status: Successful terminal status.

        Returns:
            ``PublishResult`` with ``dry_run=True``.
        """
        if final_status not in DECK_FINAL_STATUSES:
            raise ValueError(f"final_status must be one of {DECK_FINAL_STATUSES}")
        key = str(deck_id)
        logger.info(
            "InMemoryPublisher.finalize_progressive called deck_id=%s "
            "card_count=%s final_status=%s",
            deck_id,
            len(self.progressive_cards.get(key, [])),
            final_status,
        )
        cards = self.progressive_cards.setdefault(key, [])
        for row in cards:
            row["decoy_card_ids"] = list(decoys_by_card_id.get(row["card_id"], []))
        if generation_metrics is not None:
            await self.update_generation_state(
                deck_id=deck_id,
                generation_metrics=generation_metrics,
            )
        if final_status == DECK_STATUS_LIVE:
            self.live_decks.append(deck_id)
        else:
            self.ready_decks.append(deck_id)
        self.deck_statuses[key] = final_status
        self.published.append(
            {
                "deck_id": key,
                "progressive": True,
                "card_ids": [row["card_id"] for row in cards],
                "decoys": dict(decoys_by_card_id),
                "final_status": final_status,
                "generation_metrics": dict(generation_metrics or {}),
            }
        )
        return PublishResult(
            deck_id=deck_id,
            status=final_status,
            card_ids=[row["card_id"] for row in cards],
            image_paths=[row["image_path"] for row in cards],
            dry_run=True,
        )


class PostgresPublisher:
    """Atomic Postgres + filesystem publisher for live decks.

    Side effects:
        Writes images under ``data_dir/decks/<deck_id>/`` and inserts rows via
        asyncpg. Never logs credentials or image bytes.
    """

    def __init__(self, *, database_url: str, data_dir: Path) -> None:
        """Configure DSN and runtime data root.

        Args:
            database_url: Postgres DSN.
            data_dir: Runtime blob root (``DATA_DIR``).
        """
        logger.info(
            "PostgresPublisher.__init__ data_dir=%s database=%s",
            data_dir,
            database_log_meta(database_url),
        )
        self.database_url = database_url
        self.data_dir = data_dir

    async def publish(
        self,
        *,
        region_tag: str,
        cards: list[CardRecord],
        deck_id: uuid.UUID | None = None,
        final_status: str = DECK_STATUS_LIVE,
        generation_input: Mapping[str, Any] | None = None,
        generation_metrics: Mapping[str, Any] | None = None,
    ) -> PublishResult:
        """Insert cards atomically and transition a deck to ready/live.

        Args:
            region_tag: Region tag stored on ``decks.region_tag``.
            cards: Verified cards including decoy UUID arrays.
            deck_id: Optional existing ``generating`` deck UUID. When omitted,
                a new generating row is inserted.
            final_status: Successful terminal status, ``ready`` or ``live``.
            generation_input: Safe, JSON-compatible generation parameters.
            generation_metrics: Final throughput/cost counters.

        Returns:
            ``PublishResult`` with ``dry_run=False``.

        Raises:
            Exception: Propagates DB/IO errors after marking the deck failed
                when a deck row was already created.
        """
        if final_status not in DECK_FINAL_STATUSES:
            raise ValueError(f"final_status must be one of {DECK_FINAL_STATUSES}")
        resolved_deck_id = deck_id or uuid.uuid4()
        provided_deck_id = deck_id is not None
        logger.info(
            "PostgresPublisher.publish called deck_id=%s region_tag=%s "
            "card_count=%s final_status=%s provided_deck_id=%s",
            resolved_deck_id,
            region_tag,
            len(cards),
            final_status,
            provided_deck_id,
        )
        decks_root = self.data_dir / RELATIVE_DECKS_DIR
        deck_dir = decks_root / str(resolved_deck_id)
        staging_dir = decks_root / f".{resolved_deck_id}.tmp-{uuid.uuid4()}"
        image_paths: list[str] = []
        conn: asyncpg.Connection | None = None
        final_dir_created = False
        try:
            if deck_dir.exists():
                raise FileExistsError(f"deck image directory already exists: {deck_dir}")
            staging_dir.mkdir(parents=True, exist_ok=False)
            for card in cards:
                extension = image_file_extension(card.image_bytes)
                filename = f"{card.card_id}{extension}"
                rel = f"{RELATIVE_DECKS_DIR}/{resolved_deck_id}/{filename}"
                (staging_dir / filename).write_bytes(card.image_bytes)
                image_paths.append(rel)
                logger.info(
                    "PostgresPublisher staged image path=%s byte_length=%s",
                    rel,
                    len(card.image_bytes),
                )

            conn = await asyncpg.connect(dsn=self.database_url)
            async with conn.transaction():
                if provided_deck_id:
                    row = await conn.fetchrow(
                        "SELECT status FROM decks WHERE id = $1 FOR UPDATE",
                        resolved_deck_id,
                    )
                    if row is None:
                        raise LookupError(f"provided deck_id {resolved_deck_id} does not exist")
                    if row["status"] != DECK_STATUS_GENERATING:
                        raise ValueError(
                            f"provided deck_id {resolved_deck_id} must be generating, "
                            f"got {row['status']!r}"
                        )
                    await conn.execute(
                        """
                        UPDATE decks
                        SET region_tag = $1,
                            generation_input = $2::jsonb,
                            failure_reason = NULL
                        WHERE id = $3
                        """,
                        region_tag,
                        json.dumps(dict(generation_input or {})),
                        resolved_deck_id,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO decks (
                            id, region_tag, status, generation_input
                        )
                        VALUES ($1, $2, $3, $4::jsonb)
                        """,
                        resolved_deck_id,
                        region_tag,
                        DECK_STATUS_GENERATING,
                        json.dumps(dict(generation_input or {})),
                    )
                for card, rel in zip(cards, image_paths, strict=True):
                    await conn.execute(
                        """
                        INSERT INTO cards (
                            id, deck_id, concept_id, image_path, label_common,
                            decoys, verified
                        )
                        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
                        """,
                        card.card_id,
                        resolved_deck_id,
                        card.concept_id,
                        rel,
                        json.dumps(card.label_common),
                        json.dumps(card.decoy_card_ids),
                        card.verified,
                    )
                staging_dir.replace(deck_dir)
                final_dir_created = True
                await conn.execute(
                    """
                    UPDATE decks SET status = $1,
                        generation_metrics = $2::jsonb,
                        failure_reason = NULL,
                        activated_at = CASE WHEN $1 = 'live' THEN now() ELSE NULL END
                    WHERE id = $3
                    """,
                    final_status,
                    json.dumps(dict(generation_metrics or {})),
                    resolved_deck_id,
                )
            result = PublishResult(
                deck_id=resolved_deck_id,
                status=final_status,
                card_ids=[str(c.card_id) for c in cards],
                image_paths=image_paths,
                dry_run=False,
            )
            logger.info(
                "PostgresPublisher.publish completed deck_id=%s status=%s cards=%s",
                resolved_deck_id,
                result.status,
                len(result.card_ids),
            )
            return result
        except Exception as exc:
            logger.exception(
                "PostgresPublisher.publish failed deck_id=%s",
                resolved_deck_id,
            )
            shutil.rmtree(staging_dir, ignore_errors=True)
            if final_dir_created:
                shutil.rmtree(deck_dir, ignore_errors=True)
            if conn is not None:
                try:
                    reason = _safe_failure_reason(f"publication failed: {type(exc).__name__}")
                    if provided_deck_id:
                        await conn.execute(
                            """
                            UPDATE decks
                            SET status = $1, failure_reason = $2
                            WHERE id = $3 AND status = $4
                            """,
                            DECK_STATUS_FAILED,
                            reason,
                            resolved_deck_id,
                            DECK_STATUS_GENERATING,
                        )
                    else:
                        await conn.execute(
                            """
                            INSERT INTO decks (
                                id, region_tag, status, generation_input,
                                failure_reason
                            )
                            VALUES ($1, $2, $3, $4::jsonb, $5)
                            ON CONFLICT (id) DO UPDATE
                            SET status = EXCLUDED.status,
                                failure_reason = EXCLUDED.failure_reason
                            """,
                            resolved_deck_id,
                            region_tag,
                            DECK_STATUS_FAILED,
                            json.dumps(dict(generation_input or {})),
                            reason,
                        )
                except Exception:
                    logger.exception(
                        "PostgresPublisher failed to mark deck failed deck_id=%s",
                        resolved_deck_id,
                    )
            elif provided_deck_id:
                try:
                    await self.mark_failed(
                        deck_id=resolved_deck_id,
                        reason=f"publication failed: {type(exc).__name__}",
                    )
                except Exception:
                    logger.exception(
                        "PostgresPublisher could not connect to mark deck failed deck_id=%s",
                        resolved_deck_id,
                    )
            raise
        finally:
            if conn is not None:
                await conn.close()

    async def update_generation_state(
        self,
        *,
        deck_id: uuid.UUID,
        generation_input: Mapping[str, Any] | None = None,
        generation_metrics: Mapping[str, Any] | None = None,
    ) -> None:
        """Patch generation_input and/or metrics on a generating deck.

        Args:
            deck_id: Operator-created generating deck.
            generation_input: Optional full replacement input JSON.
            generation_metrics: Optional full replacement metrics JSON.

        Side effects:
            Updates only rows still in ``generating`` status.
        """
        logger.info(
            "PostgresPublisher.update_generation_state called deck_id=%s "
            "has_input=%s has_metrics=%s",
            deck_id,
            generation_input is not None,
            generation_metrics is not None,
        )
        if generation_input is None and generation_metrics is None:
            return
        conn = await asyncpg.connect(dsn=self.database_url)
        try:
            if generation_input is not None and generation_metrics is not None:
                await conn.execute(
                    """
                    UPDATE decks
                    SET generation_input = $2::jsonb,
                        generation_metrics = $3::jsonb
                    WHERE id = $1 AND status = $4
                    """,
                    deck_id,
                    json.dumps(dict(generation_input)),
                    json.dumps(dict(generation_metrics)),
                    DECK_STATUS_GENERATING,
                )
            elif generation_input is not None:
                await conn.execute(
                    """
                    UPDATE decks
                    SET generation_input = $2::jsonb
                    WHERE id = $1 AND status = $3
                    """,
                    deck_id,
                    json.dumps(dict(generation_input)),
                    DECK_STATUS_GENERATING,
                )
            else:
                await conn.execute(
                    """
                    UPDATE decks
                    SET generation_metrics = $2::jsonb
                    WHERE id = $1 AND status = $3
                    """,
                    deck_id,
                    json.dumps(dict(generation_metrics or {})),
                    DECK_STATUS_GENERATING,
                )
        finally:
            await conn.close()

    async def persist_card(
        self,
        *,
        deck_id: uuid.UUID,
        card: CardRecord,
        generation_metrics: Mapping[str, Any] | None = None,
    ) -> str:
        """Write one card image and row while the deck stays generating.

        Duplicate ``concept_id`` rows for the same deck are skipped so retries
        do not insert a second card. Empty decoys are allowed until finalize.

        Args:
            deck_id: Existing generating deck UUID.
            card: Verified card payload.
            generation_metrics: Optional progress metrics snapshot.

        Returns:
            Relative image path stored (or previously stored) for the card.
        """
        logger.info(
            "PostgresPublisher.persist_card called deck_id=%s card_id=%s "
            "concept_id=%s byte_length=%s",
            deck_id,
            card.card_id,
            card.concept_id,
            len(card.image_bytes),
        )
        extension = image_file_extension(card.image_bytes)
        filename = f"{card.card_id}{extension}"
        rel = f"{RELATIVE_DECKS_DIR}/{deck_id}/{filename}"
        deck_dir = self.data_dir / RELATIVE_DECKS_DIR / str(deck_id)
        deck_dir.mkdir(parents=True, exist_ok=True)
        image_path = deck_dir / filename

        conn = await asyncpg.connect(dsn=self.database_url)
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT status FROM decks WHERE id = $1 FOR UPDATE",
                    deck_id,
                )
                if row is None:
                    raise LookupError(f"provided deck_id {deck_id} does not exist")
                if row["status"] != DECK_STATUS_GENERATING:
                    raise ValueError(
                        f"provided deck_id {deck_id} must be generating, "
                        f"got {row['status']!r}"
                    )
                existing = await conn.fetchrow(
                    """
                    SELECT id, image_path
                    FROM cards
                    WHERE deck_id = $1 AND concept_id = $2
                    LIMIT 1
                    """,
                    deck_id,
                    card.concept_id,
                )
                if existing is not None:
                    logger.info(
                        "PostgresPublisher.persist_card skipped duplicate "
                        "concept_id=%s existing_card_id=%s",
                        card.concept_id,
                        existing["id"],
                    )
                    rel = str(existing["image_path"])
                else:
                    if not image_path.exists():
                        image_path.write_bytes(card.image_bytes)
                    await conn.execute(
                        """
                        INSERT INTO cards (
                            id, deck_id, concept_id, image_path, label_common,
                            decoys, verified
                        )
                        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
                        """,
                        card.card_id,
                        deck_id,
                        card.concept_id,
                        rel,
                        json.dumps(card.label_common),
                        json.dumps(card.decoy_card_ids),
                        card.verified,
                    )
                    logger.info(
                        "PostgresPublisher.persist_card inserted path=%s",
                        rel,
                    )
                if generation_metrics is not None:
                    await conn.execute(
                        """
                        UPDATE decks
                        SET generation_metrics = $2::jsonb
                        WHERE id = $1 AND status = $3
                        """,
                        deck_id,
                        json.dumps(dict(generation_metrics)),
                        DECK_STATUS_GENERATING,
                    )
            return rel
        finally:
            await conn.close()

    async def finalize_progressive(
        self,
        *,
        deck_id: uuid.UUID,
        decoys_by_card_id: Mapping[str, list[str]],
        generation_metrics: Mapping[str, Any] | None = None,
        final_status: str = DECK_STATUS_LIVE,
    ) -> PublishResult:
        """Backfill decoys and atomically promote a progressive deck.

        Args:
            deck_id: Generating deck with persisted cards.
            decoys_by_card_id: card UUID string → decoy card UUID strings.
            generation_metrics: Final throughput/cost counters.
            final_status: Successful terminal status.

        Returns:
            ``PublishResult`` with ``dry_run=False``.
        """
        if final_status not in DECK_FINAL_STATUSES:
            raise ValueError(f"final_status must be one of {DECK_FINAL_STATUSES}")
        logger.info(
            "PostgresPublisher.finalize_progressive called deck_id=%s "
            "decoy_map_size=%s final_status=%s",
            deck_id,
            len(decoys_by_card_id),
            final_status,
        )
        conn = await asyncpg.connect(dsn=self.database_url)
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT status FROM decks WHERE id = $1 FOR UPDATE",
                    deck_id,
                )
                if row is None:
                    raise LookupError(f"provided deck_id {deck_id} does not exist")
                if row["status"] != DECK_STATUS_GENERATING:
                    raise ValueError(
                        f"provided deck_id {deck_id} must be generating, "
                        f"got {row['status']!r}"
                    )
                cards = await conn.fetch(
                    """
                    SELECT id, image_path
                    FROM cards
                    WHERE deck_id = $1
                    ORDER BY created_at ASC, id ASC
                    """,
                    deck_id,
                )
                for card in cards:
                    decoys = list(decoys_by_card_id.get(str(card["id"]), []))
                    await conn.execute(
                        """
                        UPDATE cards
                        SET decoys = $2::jsonb
                        WHERE id = $1
                        """,
                        card["id"],
                        json.dumps(decoys),
                    )
                await conn.execute(
                    """
                    UPDATE decks SET status = $1,
                        generation_metrics = $2::jsonb,
                        failure_reason = NULL,
                        activated_at = CASE WHEN $1 = 'live' THEN now() ELSE NULL END
                    WHERE id = $3
                    """,
                    final_status,
                    json.dumps(dict(generation_metrics or {})),
                    deck_id,
                )
            result = PublishResult(
                deck_id=deck_id,
                status=final_status,
                card_ids=[str(card["id"]) for card in cards],
                image_paths=[str(card["image_path"]) for card in cards],
                dry_run=False,
            )
            logger.info(
                "PostgresPublisher.finalize_progressive completed deck_id=%s "
                "status=%s cards=%s",
                deck_id,
                result.status,
                len(result.card_ids),
            )
            return result
        finally:
            await conn.close()

    async def mark_failed(self, *, deck_id: uuid.UUID, reason: str) -> None:
        """Transition an existing generating deck to failed.

        Args:
            deck_id: Operator-created deck UUID.
            reason: Failure summary; truncated before persistence.

        Side effects:
            Updates only an existing ``generating`` row. It never inserts a
            replacement row for an unknown operator deck. Partial progressive
            cards are retained for operator diagnostics.
        """
        safe_reason = _safe_failure_reason(reason)
        logger.info(
            "PostgresPublisher.mark_failed called deck_id=%s reason_chars=%s",
            deck_id,
            len(safe_reason),
        )
        conn = await asyncpg.connect(dsn=self.database_url)
        try:
            await conn.execute(
                """
                UPDATE decks
                SET status = $1, failure_reason = $2
                WHERE id = $3 AND status = $4
                """,
                DECK_STATUS_FAILED,
                safe_reason,
                deck_id,
                DECK_STATUS_GENERATING,
            )
        finally:
            await conn.close()


def _safe_failure_reason(reason: str) -> str:
    """Normalize a bounded failure reason for database persistence.

    Args:
        reason: Exception summary or caller-provided failure description.

    Returns:
        Single-line, length-bounded text with a non-empty fallback.
    """
    logger.info("_safe_failure_reason called reason_chars=%s", len(reason))
    normalized = " ".join(reason.split()) or "deck generation failed"
    return normalized[:MAX_FAILURE_REASON_LENGTH]
