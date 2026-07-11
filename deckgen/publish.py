"""Atomic deck publication to Postgres and local deck image storage.

Writes card images with extensions derived from their encoded bytes through a
staging directory under ``data/decks/``, inserts all cards in one transaction,
then transitions the deck to ``ready`` or ``live``. Callers may publish into an
operator-pre-created ``generating`` row; that row is locked and updated rather
than reinserted. On failure, staged/final files are cleaned up and the deck is
marked ``failed`` when reachable.

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


class PostgresPublisher:
    """Atomic Postgres + filesystem publisher for live decks.

    Side effects:
        Writes PNGs under ``data_dir/decks/<deck_id>/`` and inserts rows via
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

    async def mark_failed(self, *, deck_id: uuid.UUID, reason: str) -> None:
        """Transition an existing generating deck to failed.

        Args:
            deck_id: Operator-created deck UUID.
            reason: Failure summary; truncated before persistence.

        Side effects:
            Updates only an existing ``generating`` row. It never inserts a
            replacement row for an unknown operator deck.
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
