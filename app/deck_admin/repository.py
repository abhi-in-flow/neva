"""Postgres persistence boundary for deck administration.

The repository owns SQL for deck creation, review, failure recording, and
serialized activation. It accepts an asyncpg-compatible pool so unit tests can
use isolated fakes without opening a database or mutating runtime data.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Protocol
from uuid import UUID

from app.deck_admin.config import ACTIVATION_ADVISORY_LOCK_ID, DECK_LIST_LIMIT

logger = logging.getLogger(__name__)


class DeckAdminRepository(Protocol):
    """Persistence operations required by ``DeckAdminService``."""

    async def create_generating(
        self, *, region_tag: str, generation_input: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Insert and return a generating deck."""

    async def list_decks(self) -> list[Mapping[str, Any]]:
        """Return newest deck summaries."""

    async def get_deck(self, deck_id: UUID) -> Mapping[str, Any] | None:
        """Return one deck and its cards, or None."""

    async def mark_failed(self, deck_id: UUID, failure_reason: str) -> None:
        """Set a deck to failed with a bounded safe reason."""

    async def activate(self, deck_id: UUID) -> Mapping[str, Any] | None:
        """Atomically make a ready deck the sole live deck."""


class PostgresDeckAdminRepository:
    """Asyncpg implementation of the deck administration repository."""

    def __init__(self, pool: Any) -> None:
        """Store an asyncpg-compatible pool.

        Args:
            pool: Pool exposing ``fetchrow``, ``fetch``, ``execute``, and
                ``acquire``.
        """
        logger.info("PostgresDeckAdminRepository.__init__ called")
        self._pool = pool

    async def create_generating(
        self, *, region_tag: str, generation_input: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Insert a generating deck with reproducible operator input.

        Args:
            region_tag: Validated regional deck tag.
            generation_input: Validated, credential-free request payload.

        Returns:
            Inserted deck row.

        Side effects:
            Inserts one ``decks`` row.
        """
        logger.info(
            "create_generating called region_tag=%s concept_count=%s",
            region_tag,
            len(generation_input.get("concepts", [])),
        )
        row = await self._pool.fetchrow(
            """
            INSERT INTO decks (region_tag, status, generation_input)
            VALUES ($1, 'generating', $2::jsonb)
            RETURNING id, region_tag, status, generation_metrics, failure_reason,
                      activated_at, created_at
            """,
            region_tag,
            json.dumps(dict(generation_input), ensure_ascii=False),
        )
        logger.info("create_generating completed deck_id=%s", row["id"])
        return row

    async def list_decks(self) -> list[Mapping[str, Any]]:
        """Return newest decks with card counts, capped for demo safety.

        Returns:
            Deck rows ordered newest first.
        """
        logger.info("list_decks called limit=%s", DECK_LIST_LIMIT)
        rows = await self._pool.fetch(
            """
            SELECT d.id, d.region_tag, d.status, d.generation_metrics,
                   d.failure_reason, d.activated_at, d.created_at,
                   COUNT(c.id)::int AS card_count
            FROM decks d
            LEFT JOIN cards c ON c.deck_id = d.id
            GROUP BY d.id
            ORDER BY d.created_at DESC, d.id DESC
            LIMIT $1
            """,
            DECK_LIST_LIMIT,
        )
        logger.info("list_decks completed deck_count=%s", len(rows))
        return list(rows)

    async def get_deck(self, deck_id: UUID) -> Mapping[str, Any] | None:
        """Return one deck with generation input and review card rows.

        Args:
            deck_id: Target deck UUID.

        Returns:
            A mapping with a ``cards`` list, or None when absent.
        """
        logger.info("get_deck called deck_id=%s", deck_id)
        deck = await self._pool.fetchrow(
            """
            SELECT d.id, d.region_tag, d.status, d.generation_input,
                   d.generation_metrics, d.failure_reason, d.activated_at,
                   d.created_at, COUNT(c.id)::int AS card_count
            FROM decks d
            LEFT JOIN cards c ON c.deck_id = d.id
            WHERE d.id = $1
            GROUP BY d.id
            """,
            deck_id,
        )
        if deck is None:
            logger.info("get_deck not_found deck_id=%s", deck_id)
            return None
        cards = await self._pool.fetch(
            """
            SELECT id, concept_id, image_path, label_common, verified
            FROM cards
            WHERE deck_id = $1
            ORDER BY created_at ASC, id ASC
            """,
            deck_id,
        )
        result = dict(deck)
        result["cards"] = list(cards)
        logger.info(
            "get_deck completed deck_id=%s card_count=%s", deck_id, len(cards)
        )
        return result

    async def mark_failed(self, deck_id: UUID, failure_reason: str) -> None:
        """Mark generation failed without exposing exception internals to clients.

        Args:
            deck_id: Failed deck UUID.
            failure_reason: Already bounded, safe failure description.

        Side effects:
            Updates the target deck unless it has already become live.
        """
        logger.info(
            "mark_failed called deck_id=%s reason_chars=%s",
            deck_id,
            len(failure_reason),
        )
        await self._pool.execute(
            """
            UPDATE decks
            SET status = 'failed', failure_reason = $2
            WHERE id = $1 AND status <> 'live'
            """,
            deck_id,
            failure_reason,
        )
        logger.info("mark_failed completed deck_id=%s", deck_id)

    async def activate(self, deck_id: UUID) -> Mapping[str, Any] | None:
        """Serialize activation and preserve exactly one live deck.

        Args:
            deck_id: Ready or already-live target UUID.

        Returns:
            Mapping containing the original target status and resulting row, or
            None when the target does not exist.

        Side effects:
            Takes a transaction advisory lock, locks the target row, demotes
            other live decks, and promotes the target.
        """
        logger.info("activate called deck_id=%s", deck_id)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock($1)",
                    ACTIVATION_ADVISORY_LOCK_ID,
                )
                target = await connection.fetchrow(
                    """
                    SELECT id, status
                    FROM decks
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    deck_id,
                )
                if target is None:
                    logger.info("activate not_found deck_id=%s", deck_id)
                    return None
                original_status = target["status"]
                if original_status not in {"ready", "live"}:
                    logger.info(
                        "activate conflict deck_id=%s status=%s",
                        deck_id,
                        original_status,
                    )
                    return {"original_status": original_status, "deck": None}
                await connection.execute(
                    """
                    UPDATE decks
                    SET status = 'ready', activated_at = NULL
                    WHERE status = 'live' AND id <> $1
                    """,
                    deck_id,
                )
                deck = await connection.fetchrow(
                    """
                    UPDATE decks
                    SET status = 'live',
                        activated_at = COALESCE(activated_at, now()),
                        failure_reason = NULL
                    WHERE id = $1
                    RETURNING id, region_tag, status, generation_metrics,
                              failure_reason, activated_at, created_at
                    """,
                    deck_id,
                )
        logger.info(
            "activate completed deck_id=%s original_status=%s",
            deck_id,
            original_status,
        )
        return {"original_status": original_status, "deck": deck}
