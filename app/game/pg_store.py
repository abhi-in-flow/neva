"""Postgres GameStore adapter using parameterized asyncpg SQL.

Implements transactional matchmaking with ``FOR UPDATE SKIP LOCKED``,
idempotent job inserts via the unique ``(kind, turn_id)`` index, and a single
round-trip state bundle query suitable for 2-second client polling. Queue
heartbeats refresh ``enqueued_at`` on every ``pair/request``; rows older than
the configured activity TTL are evicted inside the matchmaking transaction so
abandoned test players cannot be selected. Player nicknames are reserved
case-insensitively with insert-retry on unique violations (never a
read-then-write race). All SQL is parameterized; callers never interpolate
user input into statements.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from app.game.config import get_game_config
from app.game.nicknames import allocate_nickname_candidate
from app.game.types import (
    CardRecord,
    LeaderboardRow,
    MetricsAggregateRow,
    MetricsSnapshot,
    PairRecord,
    PlayerRecord,
    PlayerStats,
    StateBundle,
    TurnRecord,
    metrics_snapshot_from_aggregates,
    normalize_common_langs,
    resolve_label_text,
)

logger = logging.getLogger(__name__)

# Unique index created in schema.sql / migration 0005.
_NICKNAME_UNIQUE_INDEX = "players_nickname_lower_uidx"


def _parse_player(row: asyncpg.Record) -> PlayerRecord:
    """Map a players row into a ``PlayerRecord``.

    Args:
        row: asyncpg record from ``players``.

    Returns:
        Typed player record.
    """
    return PlayerRecord(
        id=row["id"],
        nickname=row["nickname"],
        native_lang=row["native_lang"],
        common_langs=normalize_common_langs(row["common_langs"]),
        session_token_hash=row["session_token_hash"],
        created_at=row["created_at"],
    )


def _parse_pair(row: asyncpg.Record) -> PairRecord:
    """Map a pairs row into a ``PairRecord``."""
    return PairRecord(
        id=row["id"],
        player_a=row["player_a"],
        player_b=row["player_b"],
        common_lang=row["common_lang"],
        status=row["status"],
        created_at=row["created_at"],
    )


def _parse_card(row: asyncpg.Record) -> CardRecord:
    """Map a cards row into a ``CardRecord``."""
    label = row["label_common"]
    if isinstance(label, str):
        label = json.loads(label)
    decoys = row["decoys"]
    if isinstance(decoys, str):
        decoys = json.loads(decoys)
    return CardRecord(
        id=row["id"],
        deck_id=row["deck_id"],
        image_path=row["image_path"],
        label_common=dict(label),
        decoys=[str(x) for x in decoys],
        verified=bool(row["verified"]),
    )


def _parse_turn(row: asyncpg.Record) -> TurnRecord:
    """Map a turns row into a ``TurnRecord``."""
    quality = row["quality"]
    if isinstance(quality, str):
        quality = json.loads(quality)
    duration = row["duration_s"]
    return TurnRecord(
        id=row["id"],
        pair_id=row["pair_id"],
        speaker_id=row["speaker_id"],
        guesser_id=row["guesser_id"],
        card_id=row["card_id"],
        status=row["status"],
        audio_path=row["audio_path"],
        audio_flac_path=row["audio_flac_path"],
        duration_s=float(duration) if duration is not None else None,
        quality=dict(quality) if quality is not None else None,
        attempts=int(row["attempts"]),
        outcome=row["outcome"],
        created_at=row["created_at"],
    )


class PostgresGameStore:
    """asyncpg-backed store used by the live FastAPI process."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Bind this store to a shared connection pool.

        Args:
            pool: Application asyncpg pool from ``app.state.pool``.
        """
        logger.info("PostgresGameStore.__init__ called")
        self._pool = pool

    async def create_player(
        self,
        *,
        nickname: str,
        native_lang: str,
        common_langs: list[str],
        session_token_hash: str,
    ) -> PlayerRecord:
        """Insert a player row with a case-insensitively unique nickname.

        Tries the requested friendly name first. On a uniqueness collision,
        retries with a compact ``#N`` suffix. Each INSERT runs as its own
        statement against the pool so a UniqueViolation does not abort a
        longer transaction.

        Args:
            nickname: Preferred display name (1–32 chars).
            native_lang: Declared native language.
            common_langs: Shared languages list.
            session_token_hash: SHA-256 hex of the bearer token.

        Returns:
            Created player record (nickname may include a collision suffix).

        Raises:
            RuntimeError: When nickname allocation exhausts retry budget.
        """
        cfg = get_game_config()
        max_attempts = cfg.nickname_alloc_max_attempts
        logger.info(
            "PostgresGameStore.create_player called nickname_len=%s native_lang=%s "
            "common_count=%s max_attempts=%s",
            len(nickname),
            native_lang,
            len(common_langs),
            max_attempts,
        )
        langs_json = json.dumps(common_langs)
        last_error: BaseException | None = None
        for attempt in range(max_attempts):
            candidate = allocate_nickname_candidate(nickname, attempt)
            try:
                # Separate pool statement per attempt: UniqueViolation aborts
                # only this implicit transaction, never a caller-held one.
                row = await self._pool.fetchrow(
                    """
                    INSERT INTO players (
                        nickname, native_lang, common_langs, session_token_hash
                    )
                    VALUES ($1, $2, $3::jsonb, $4)
                    RETURNING id, nickname, native_lang, common_langs,
                              session_token_hash, created_at
                    """,
                    candidate,
                    native_lang,
                    langs_json,
                    session_token_hash,
                )
            except asyncpg.UniqueViolationError as exc:
                last_error = exc
                constraint = exc.constraint_name or ""
                if constraint and constraint != _NICKNAME_UNIQUE_INDEX:
                    logger.info(
                        "PostgresGameStore.create_player unique_violation "
                        "constraint=%s attempt=%s (non-nickname)",
                        constraint,
                        attempt,
                    )
                    raise
                logger.info(
                    "PostgresGameStore.create_player nickname_collision "
                    "attempt=%s candidate_len=%s constraint=%s",
                    attempt,
                    len(candidate),
                    constraint or "unknown",
                )
                continue
            assert row is not None
            logger.info(
                "PostgresGameStore.create_player completed player_id=%s "
                "attempt=%s nickname_len=%s exact=%s",
                row["id"],
                attempt,
                len(row["nickname"]),
                attempt == 0,
            )
            return _parse_player(row)
        logger.info(
            "PostgresGameStore.create_player exhausted nickname_len=%s "
            "max_attempts=%s",
            len(nickname),
            max_attempts,
        )
        raise RuntimeError("unable to allocate a unique nickname") from last_error

    async def get_player_by_token_hash(self, token_hash: str) -> PlayerRecord | None:
        """Lookup a player by hashed bearer token."""
        logger.info(
            "PostgresGameStore.get_player_by_token_hash called hash_prefix=%s",
            token_hash[:8],
        )
        row = await self._pool.fetchrow(
            """
            SELECT id, nickname, native_lang, common_langs, session_token_hash, created_at
            FROM players
            WHERE session_token_hash = $1
            """,
            token_hash,
        )
        return _parse_player(row) if row else None

    async def get_player(self, player_id: UUID) -> PlayerRecord | None:
        """Lookup a player by id."""
        logger.info("PostgresGameStore.get_player called player_id=%s", player_id)
        row = await self._pool.fetchrow(
            """
            SELECT id, nickname, native_lang, common_langs, session_token_hash, created_at
            FROM players
            WHERE id = $1
            """,
            player_id,
        )
        return _parse_player(row) if row else None

    async def enqueue_player(self, player_id: UUID) -> None:
        """Insert or refresh the player in the matchmaking queue.

        Every call (including re-requests from the frontend heartbeat) updates
        ``enqueued_at`` to ``now()`` so active waiters stay within the queue
        activity TTL. Already-paired players are not enqueued by the service.

        Args:
            player_id: Player to place or refresh in the queue.
        """
        logger.info("PostgresGameStore.enqueue_player called player_id=%s", player_id)
        await self._pool.execute(
            """
            INSERT INTO matchmaking_queue (player_id, enqueued_at)
            VALUES ($1, now())
            ON CONFLICT (player_id) DO UPDATE
            SET enqueued_at = excluded.enqueued_at
            """,
            player_id,
        )

    async def try_match(self, player_id: UUID) -> PairRecord | None:
        """Claim a compatible partner with transactional SKIP LOCKED.

        Match rules: shared common language, different native language, never
        self. Prefer partners not previously paired when alternatives exist.
        Before scanning candidates, evict queue rows older than the configured
        activity TTL so abandoned waiters cannot be selected. Active pairs are
        returned immediately without touching the queue.

        Args:
            player_id: Player requesting a match (must already be queued).

        Returns:
            New or existing active pair, or ``None`` when still waiting.
        """
        cfg = get_game_config()
        ttl = timedelta(seconds=cfg.matchmaking_queue_ttl_seconds)
        logger.info(
            "PostgresGameStore.try_match called player_id=%s queue_ttl_s=%s",
            player_id,
            cfg.matchmaking_queue_ttl_seconds,
        )
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    """
                    SELECT id, player_a, player_b, common_lang, status, created_at
                    FROM pairs
                    WHERE status = 'active'
                      AND (player_a = $1 OR player_b = $1)
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    player_id,
                )
                if existing:
                    return _parse_pair(existing)

                # Refresh heartbeat inside the match txn (pair/request already
                # called enqueue_player; this keeps SKIP LOCKED races consistent).
                await conn.execute(
                    """
                    INSERT INTO matchmaking_queue (player_id, enqueued_at)
                    VALUES ($1, now())
                    ON CONFLICT (player_id) DO UPDATE
                    SET enqueued_at = excluded.enqueued_at
                    """,
                    player_id,
                )

                # Evict stale waiters before candidate selection (race-safe:
                # same transaction as FOR UPDATE SKIP LOCKED claim).
                evicted = await conn.fetch(
                    """
                    DELETE FROM matchmaking_queue
                    WHERE enqueued_at < now() - $1::interval
                      AND player_id <> $2
                    RETURNING player_id
                    """,
                    ttl,
                    player_id,
                )
                if evicted:
                    logger.info(
                        "PostgresGameStore.try_match evicted_stale count=%s "
                        "player_id=%s",
                        len(evicted),
                        player_id,
                    )

                me = await conn.fetchrow(
                    """
                    SELECT id, nickname, native_lang, common_langs, session_token_hash, created_at
                    FROM players
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    player_id,
                )
                if me is None:
                    return None
                my_langs = normalize_common_langs(me["common_langs"])

                # Lock self queue row so concurrent matchers serialize on us.
                await conn.fetchrow(
                    """
                    SELECT player_id
                    FROM matchmaking_queue
                    WHERE player_id = $1
                    FOR UPDATE
                    """,
                    player_id,
                )

                partner = await conn.fetchrow(
                    """
                    WITH candidates AS (
                        SELECT
                            q.player_id,
                            q.enqueued_at,
                            p.native_lang,
                            p.common_langs,
                            EXISTS (
                                SELECT 1
                                FROM pairs pr
                                WHERE (pr.player_a = $1 AND pr.player_b = q.player_id)
                                   OR (pr.player_b = $1 AND pr.player_a = q.player_id)
                            ) AS previously_paired
                        FROM matchmaking_queue q
                        JOIN players p ON p.id = q.player_id
                        WHERE q.player_id <> $1
                          AND q.enqueued_at >= now() - $4::interval
                          AND p.native_lang <> $2
                          AND EXISTS (
                              SELECT 1
                              FROM jsonb_array_elements_text(p.common_langs) AS lang
                              WHERE lang = ANY($3::text[])
                          )
                        ORDER BY q.enqueued_at ASC
                        FOR UPDATE OF q SKIP LOCKED
                    )
                    SELECT *
                    FROM candidates
                    ORDER BY previously_paired ASC, enqueued_at ASC
                    LIMIT 1
                    """,
                    player_id,
                    me["native_lang"],
                    my_langs,
                    ttl,
                )
                if partner is None:
                    logger.info("PostgresGameStore.try_match waiting player_id=%s", player_id)
                    return None

                partner_langs = normalize_common_langs(partner["common_langs"])
                shared = [lang for lang in my_langs if lang in set(partner_langs)]
                common_lang = shared[0]
                # Earlier enqueued player speaks first.
                my_queue = await conn.fetchrow(
                    "SELECT enqueued_at FROM matchmaking_queue WHERE player_id = $1",
                    player_id,
                )
                if my_queue and my_queue["enqueued_at"] <= partner["enqueued_at"]:
                    player_a, player_b = player_id, partner["player_id"]
                else:
                    player_a, player_b = partner["player_id"], player_id

                pair_row = await conn.fetchrow(
                    """
                    INSERT INTO pairs (player_a, player_b, common_lang, status)
                    VALUES ($1, $2, $3, 'active')
                    RETURNING id, player_a, player_b, common_lang, status, created_at
                    """,
                    player_a,
                    player_b,
                    common_lang,
                )
                assert pair_row is not None
                await conn.execute(
                    "DELETE FROM matchmaking_queue WHERE player_id = ANY($1::uuid[])",
                    [player_id, partner["player_id"]],
                )

                card_row = await self._pick_card_conn(conn, pair_row["id"])
                if card_row is not None:
                    await conn.fetchrow(
                        """
                        INSERT INTO turns (
                            pair_id, speaker_id, guesser_id, card_id, status
                        )
                        VALUES ($1, $2, $3, $4, 'awaiting_audio')
                        RETURNING id
                        """,
                        pair_row["id"],
                        player_a,
                        player_b,
                        card_row["id"],
                    )
                logger.info(
                    "PostgresGameStore.try_match matched pair_id=%s common_lang=%s",
                    pair_row["id"],
                    common_lang,
                )
                return _parse_pair(pair_row)

    async def _pick_card_conn(
        self,
        conn: asyncpg.Connection,
        pair_id: UUID,
    ) -> asyncpg.Record | None:
        """Select a verified live card inside an open transaction.

        Args:
            conn: Open asyncpg connection.
            pair_id: Pair that will consume the card.

        Returns:
            Card row or ``None``.
        """
        logger.info("PostgresGameStore._pick_card_conn called pair_id=%s", pair_id)
        row = await conn.fetchrow(
            """
            WITH live AS (
                SELECT c.id, c.deck_id, c.image_path, c.label_common, c.decoys, c.verified,
                       (c.id IN (
                            SELECT t.card_id FROM turns t WHERE t.pair_id = $1
                       )) AS used
                FROM cards c
                JOIN decks d ON d.id = c.deck_id
                WHERE d.status = 'live' AND c.verified = TRUE
            )
            SELECT id, deck_id, image_path, label_common, decoys, verified
            FROM live
            ORDER BY used ASC, random()
            LIMIT 1
            """,
            pair_id,
        )
        return row

    async def get_active_pair(self, player_id: UUID) -> PairRecord | None:
        """Return the player's active pair if any."""
        logger.info("PostgresGameStore.get_active_pair called player_id=%s", player_id)
        row = await self._pool.fetchrow(
            """
            SELECT id, player_a, player_b, common_lang, status, created_at
            FROM pairs
            WHERE status = 'active'
              AND (player_a = $1 OR player_b = $1)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            player_id,
        )
        return _parse_pair(row) if row else None

    async def create_turn(
        self,
        *,
        pair_id: UUID,
        speaker_id: UUID,
        guesser_id: UUID,
        card_id: UUID,
    ) -> TurnRecord:
        """Insert a new awaiting-audio turn."""
        logger.info(
            "PostgresGameStore.create_turn called pair_id=%s speaker_id=%s "
            "guesser_id=%s card_id=%s",
            pair_id,
            speaker_id,
            guesser_id,
            card_id,
        )
        row = await self._pool.fetchrow(
            """
            INSERT INTO turns (pair_id, speaker_id, guesser_id, card_id, status)
            VALUES ($1, $2, $3, $4, 'awaiting_audio')
            RETURNING id, pair_id, speaker_id, guesser_id, card_id, status,
                      audio_path, audio_flac_path, duration_s, quality, attempts,
                      outcome, created_at
            """,
            pair_id,
            speaker_id,
            guesser_id,
            card_id,
        )
        assert row is not None
        return _parse_turn(row)

    async def pick_card_for_pair(self, pair_id: UUID) -> CardRecord | None:
        """Choose a verified live-deck card, preferring unused cards."""
        logger.info("PostgresGameStore.pick_card_for_pair called pair_id=%s", pair_id)
        async with self._pool.acquire() as conn:
            row = await self._pick_card_conn(conn, pair_id)
        return _parse_card(row) if row else None

    async def get_turn(self, turn_id: UUID) -> TurnRecord | None:
        """Fetch a turn by id."""
        logger.info("PostgresGameStore.get_turn called turn_id=%s", turn_id)
        row = await self._pool.fetchrow(
            """
            SELECT id, pair_id, speaker_id, guesser_id, card_id, status,
                   audio_path, audio_flac_path, duration_s, quality, attempts,
                   outcome, created_at
            FROM turns
            WHERE id = $1
            """,
            turn_id,
        )
        return _parse_turn(row) if row else None

    async def get_latest_turn(self, pair_id: UUID) -> TurnRecord | None:
        """Fetch the newest turn for a pair."""
        logger.info("PostgresGameStore.get_latest_turn called pair_id=%s", pair_id)
        row = await self._pool.fetchrow(
            """
            SELECT id, pair_id, speaker_id, guesser_id, card_id, status,
                   audio_path, audio_flac_path, duration_s, quality, attempts,
                   outcome, created_at
            FROM turns
            WHERE pair_id = $1
            ORDER BY (status <> 'scored') DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            pair_id,
        )
        return _parse_turn(row) if row else None

    async def get_card(self, card_id: UUID) -> CardRecord | None:
        """Fetch a card by id."""
        logger.info("PostgresGameStore.get_card called card_id=%s", card_id)
        row = await self._pool.fetchrow(
            """
            SELECT id, deck_id, image_path, label_common, decoys, verified
            FROM cards
            WHERE id = $1
            """,
            card_id,
        )
        return _parse_card(row) if row else None

    async def get_cards(self, card_ids: list[UUID]) -> list[CardRecord]:
        """Fetch many cards by id."""
        logger.info("PostgresGameStore.get_cards called count=%s", len(card_ids))
        if not card_ids:
            return []
        rows = await self._pool.fetch(
            """
            SELECT id, deck_id, image_path, label_common, decoys, verified
            FROM cards
            WHERE id = ANY($1::uuid[])
            """,
            card_ids,
        )
        return [_parse_card(row) for row in rows]

    async def accept_audio(
        self,
        *,
        turn_id: UUID,
        audio_path: str,
        duration_s: float,
    ) -> TurnRecord:
        """Persist accepted audio and move turn to label confirmation."""
        logger.info(
            "PostgresGameStore.accept_audio called turn_id=%s duration_s=%s",
            turn_id,
            duration_s,
        )
        row = await self._pool.fetchrow(
            """
            UPDATE turns
            SET audio_path = $2,
                duration_s = $3,
                status = 'awaiting_label_confirm'
            WHERE id = $1 AND status = 'awaiting_audio'
            RETURNING id, pair_id, speaker_id, guesser_id, card_id, status,
                      audio_path, audio_flac_path, duration_s, quality, attempts,
                      outcome, created_at
            """,
            turn_id,
            audio_path,
            duration_s,
        )
        if row is None:
            raise ValueError("turn not awaiting audio")
        return _parse_turn(row)

    async def enqueue_job(self, *, kind: str, turn_id: UUID) -> bool:
        """Idempotently insert a job using the unique turn/kind index.

        Args:
            kind: ``triage`` or ``package``.
            turn_id: Target turn.

        Returns:
            ``True`` when a new row was inserted.
        """
        logger.info(
            "PostgresGameStore.enqueue_job called kind=%s turn_id=%s",
            kind,
            turn_id,
        )
        # A targetless conflict handler is race-safe and does not depend on
        # expression-index inference details across Postgres builds.
        row = await self._pool.fetchrow(
            """
            INSERT INTO jobs (kind, payload, status)
            VALUES ($1, jsonb_build_object('turn_id', $2::text), 'pending')
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            kind,
            str(turn_id),
        )
        return row is not None

    async def confirm_label(self, turn_id: UUID) -> TurnRecord:
        """Move turn from label confirmation to guessing."""
        logger.info("PostgresGameStore.confirm_label called turn_id=%s", turn_id)
        row = await self._pool.fetchrow(
            """
            UPDATE turns
            SET status = 'awaiting_guess'
            WHERE id = $1 AND status = 'awaiting_label_confirm'
            RETURNING id, pair_id, speaker_id, guesser_id, card_id, status,
                      audio_path, audio_flac_path, duration_s, quality, attempts,
                      outcome, created_at
            """,
            turn_id,
        )
        if row is None:
            raise ValueError("turn not awaiting label confirm")
        return _parse_turn(row)

    async def apply_guess(self, *, turn_id: UUID, correct: bool) -> TurnRecord:
        """Apply one guess attempt inside a single UPDATE."""
        logger.info(
            "PostgresGameStore.apply_guess called turn_id=%s correct=%s",
            turn_id,
            correct,
        )
        cfg = get_game_config()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT id, pair_id, speaker_id, guesser_id, card_id, status,
                           audio_path, audio_flac_path, duration_s, quality, attempts,
                           outcome, created_at
                    FROM turns
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    turn_id,
                )
                if row is None or row["status"] != "awaiting_guess":
                    raise ValueError("turn not awaiting guess")
                attempts = int(row["attempts"]) + 1
                if correct:
                    status, outcome = "scored", "validated"
                    await conn.execute(
                        """
                        INSERT INTO metrics_counters (key, value, updated_at)
                        VALUES ('validated_pairs', 1, now())
                        ON CONFLICT (key) DO UPDATE
                        SET value = metrics_counters.value + 1,
                            updated_at = now()
                        """,
                    )
                elif attempts >= cfg.max_guess_attempts:
                    status, outcome = "scored", "unclear"
                else:
                    status, outcome = "awaiting_guess", "pending"
                updated = await conn.fetchrow(
                    """
                    UPDATE turns
                    SET attempts = $2, status = $3, outcome = $4
                    WHERE id = $1
                    RETURNING id, pair_id, speaker_id, guesser_id, card_id, status,
                              audio_path, audio_flac_path, duration_s, quality, attempts,
                              outcome, created_at
                    """,
                    turn_id,
                    attempts,
                    status,
                    outcome,
                )
                assert updated is not None
                return _parse_turn(updated)

    async def set_turn_quality_for_tests(
        self,
        turn_id: UUID,
        quality: dict[str, Any],
    ) -> TurnRecord:
        """Attach quality JSON for tests that exercise package enqueue."""
        logger.info(
            "PostgresGameStore.set_turn_quality_for_tests called turn_id=%s",
            turn_id,
        )
        row = await self._pool.fetchrow(
            """
            UPDATE turns
            SET quality = $2::jsonb
            WHERE id = $1
            RETURNING id, pair_id, speaker_id, guesser_id, card_id, status,
                      audio_path, audio_flac_path, duration_s, quality, attempts,
                      outcome, created_at
            """,
            turn_id,
            json.dumps(quality),
        )
        if row is None:
            raise ValueError("turn not found")
        return _parse_turn(row)

    async def complete_pair_if_capped(self, *, pair_id: UUID, rounds_cap: int) -> bool:
        """Mark pair completed when either player reached the session cap."""
        logger.info(
            "PostgresGameStore.complete_pair_if_capped called pair_id=%s rounds_cap=%s",
            pair_id,
            rounds_cap,
        )
        result = await self._pool.execute(
            """
            UPDATE pairs
            SET status = 'completed'
            WHERE id = $1
              AND status = 'active'
              AND EXISTS (
                  SELECT 1
                  FROM players pl
                  WHERE pl.id IN (pairs.player_a, pairs.player_b)
                    AND (
                        SELECT COUNT(*)::int
                        FROM turns t
                        WHERE t.status = 'scored'
                          AND (t.speaker_id = pl.id OR t.guesser_id = pl.id)
                    ) >= $2
              )
            """,
            pair_id,
            rounds_cap,
        )
        return result.endswith("1")

    async def fetch_state_bundle(
        self,
        player_id: UUID,
        *,
        leaderboard_top: int,
    ) -> StateBundle:
        """Load ``/api/state`` facts in a single Postgres round trip.

        Args:
            player_id: Authenticated player.
            leaderboard_top: Embedded leaderboard size.

        Returns:
            Populated ``StateBundle``.
        """
        logger.info(
            "PostgresGameStore.fetch_state_bundle called player_id=%s top=%s",
            player_id,
            leaderboard_top,
        )
        cfg = get_game_config()
        row = await self._pool.fetchrow(
            """
            WITH me AS (
                SELECT id, nickname, native_lang, common_langs, session_token_hash, created_at
                FROM players
                WHERE id = $1
            ),
            queued AS (
                SELECT EXISTS(
                    SELECT 1
                    FROM matchmaking_queue
                    WHERE player_id = $1
                      AND enqueued_at >= now() - $4::interval
                ) AS is_queued
            ),
            active_pair AS (
                SELECT p.id, p.player_a, p.player_b, p.common_lang, p.status, p.created_at
                FROM pairs p
                WHERE p.status = 'active'
                  AND (p.player_a = $1 OR p.player_b = $1)
                ORDER BY p.created_at DESC
                LIMIT 1
            ),
            partner AS (
                SELECT pl.id, pl.nickname, pl.native_lang, pl.common_langs,
                       pl.session_token_hash, pl.created_at
                FROM players pl
                JOIN active_pair ap ON pl.id = CASE
                    WHEN ap.player_a = $1 THEN ap.player_b ELSE ap.player_a
                END
            ),
            latest_turn AS (
                SELECT t.id, t.pair_id, t.speaker_id, t.guesser_id, t.card_id, t.status,
                       t.audio_path, t.audio_flac_path, t.duration_s, t.quality, t.attempts,
                       t.outcome, t.created_at
                FROM turns t
                JOIN active_pair ap ON ap.id = t.pair_id
                ORDER BY (t.status <> 'scored') DESC, t.created_at DESC, t.id DESC
                LIMIT 1
            ),
            prev_scored AS (
                SELECT t.id, t.pair_id, t.speaker_id, t.guesser_id, t.card_id, t.status,
                       t.audio_path, t.audio_flac_path, t.duration_s, t.quality, t.attempts,
                       t.outcome, t.created_at
                FROM turns t
                JOIN active_pair ap ON ap.id = t.pair_id
                WHERE t.status = 'scored'
                ORDER BY t.created_at DESC, t.id DESC
                LIMIT 1
            ),
            card AS (
                SELECT c.id, c.deck_id, c.image_path, c.label_common, c.decoys, c.verified
                FROM cards c
                JOIN latest_turn lt ON lt.card_id = c.id
            ),
            my_stats AS (
                SELECT
                    COALESCE(SUM(
                        CASE WHEN t.outcome = 'validated' THEN $2 ELSE 0 END
                    ), 0)::int AS score,
                    COALESCE(SUM(
                        CASE WHEN t.status = 'scored' THEN 1 ELSE 0 END
                    ), 0)::int AS rounds_played
                FROM turns t
                WHERE t.speaker_id = $1 OR t.guesser_id = $1
            ),
            scores AS (
                SELECT pl.id,
                       pl.nickname,
                       COALESCE(SUM(
                           CASE WHEN t.outcome = 'validated' THEN $2 ELSE 0 END
                       ), 0)::int AS score
                FROM players pl
                LEFT JOIN turns t
                  ON t.speaker_id = pl.id OR t.guesser_id = pl.id
                GROUP BY pl.id, pl.nickname
            ),
            ranked AS (
                SELECT id, nickname, score,
                       RANK() OVER (ORDER BY score DESC, nickname ASC) AS rank
                FROM scores
            ),
            board AS (
                SELECT nickname, score
                FROM ranked
                ORDER BY score DESC, nickname ASC
                LIMIT $3
            )
            SELECT
                (SELECT row_to_json(me.*) FROM me) AS player,
                (SELECT is_queued FROM queued) AS queued,
                (SELECT row_to_json(active_pair.*) FROM active_pair) AS pair,
                (SELECT row_to_json(partner.*) FROM partner) AS partner,
                (SELECT row_to_json(latest_turn.*) FROM latest_turn) AS turn,
                (SELECT row_to_json(card.*) FROM card) AS card,
                (SELECT row_to_json(prev_scored.*) FROM prev_scored) AS previous_scored,
                (SELECT score FROM my_stats) AS score,
                (SELECT rounds_played FROM my_stats) AS rounds_played,
                (SELECT rank FROM ranked WHERE id = $1) AS rank,
                COALESCE(
                    (SELECT json_agg(row_to_json(board.*) ORDER BY board.score DESC)
                     FROM board),
                    '[]'::json
                ) AS leaderboard_top
            """,
            player_id,
            cfg.points_per_validation,
            leaderboard_top,
            timedelta(seconds=cfg.matchmaking_queue_ttl_seconds),
        )
        assert row is not None
        player_data = row["player"]
        if isinstance(player_data, str):
            player_data = json.loads(player_data)
        player = PlayerRecord(
            id=UUID(str(player_data["id"])),
            nickname=player_data["nickname"],
            native_lang=player_data["native_lang"],
            common_langs=normalize_common_langs(player_data["common_langs"]),
            session_token_hash=player_data["session_token_hash"],
            created_at=_parse_dt(player_data["created_at"]),
        )

        pair = None
        if row["pair"]:
            pair_data = row["pair"]
            if isinstance(pair_data, str):
                pair_data = json.loads(pair_data)
            pair = PairRecord(
                id=UUID(str(pair_data["id"])),
                player_a=UUID(str(pair_data["player_a"])),
                player_b=UUID(str(pair_data["player_b"])),
                common_lang=pair_data["common_lang"],
                status=pair_data["status"],
                created_at=_parse_dt(pair_data["created_at"]),
            )

        partner = None
        if row["partner"]:
            partner_data = row["partner"]
            if isinstance(partner_data, str):
                partner_data = json.loads(partner_data)
            partner = PlayerRecord(
                id=UUID(str(partner_data["id"])),
                nickname=partner_data["nickname"],
                native_lang=partner_data["native_lang"],
                common_langs=normalize_common_langs(partner_data["common_langs"]),
                session_token_hash=partner_data["session_token_hash"],
                created_at=_parse_dt(partner_data["created_at"]),
            )

        turn = _turn_from_json(row["turn"])
        previous_scored = _turn_from_json(row["previous_scored"])
        card = None
        decoy_labels: dict[str, str] = {}
        if row["card"]:
            card_data = row["card"]
            if isinstance(card_data, str):
                card_data = json.loads(card_data)
            card = CardRecord(
                id=UUID(str(card_data["id"])),
                deck_id=UUID(str(card_data["deck_id"])),
                image_path=card_data["image_path"],
                label_common=(
                    json.loads(card_data["label_common"])
                    if isinstance(card_data["label_common"], str)
                    else dict(card_data["label_common"])
                ),
                decoys=[
                    str(x)
                    for x in (
                        json.loads(card_data["decoys"])
                        if isinstance(card_data["decoys"], str)
                        else card_data["decoys"]
                    )
                ],
                verified=bool(card_data["verified"]),
            )
            if pair is not None and card.decoys:
                decoy_rows = await self.get_cards([UUID(x) for x in card.decoys])
                for decoy in decoy_rows:
                    decoy_labels[str(decoy.id)] = resolve_label_text(
                        decoy.label_common,
                        pair.common_lang,
                    )

        board_raw = row["leaderboard_top"]
        if isinstance(board_raw, str):
            board_raw = json.loads(board_raw)
        leaderboard_top_rows = [
            LeaderboardRow(nickname=item["nickname"], score=int(item["score"]))
            for item in (board_raw or [])
        ]
        return StateBundle(
            player=player,
            queued=bool(row["queued"]),
            pair=pair,
            partner=partner,
            turn=turn,
            card=card,
            previous_scored=previous_scored,
            stats=PlayerStats(
                score=int(row["score"] or 0),
                rounds_played=int(row["rounds_played"] or 0),
                rank=int(row["rank"]) if row["rank"] is not None else None,
            ),
            leaderboard_top=leaderboard_top_rows,
            decoy_labels=decoy_labels,
        )

    async def player_stats(self, player_id: UUID) -> PlayerStats:
        """Compute score, rounds played, and rank for a player."""
        logger.info("PostgresGameStore.player_stats called player_id=%s", player_id)
        cfg = get_game_config()
        row = await self._pool.fetchrow(
            """
            WITH scores AS (
                SELECT pl.id,
                       pl.nickname,
                       COALESCE(SUM(
                           CASE WHEN t.outcome = 'validated' THEN $2 ELSE 0 END
                       ), 0)::int AS score,
                       COALESCE(SUM(
                           CASE WHEN t.status = 'scored'
                                 AND (t.speaker_id = pl.id OR t.guesser_id = pl.id)
                                THEN 1 ELSE 0 END
                       ), 0)::int AS rounds_played
                FROM players pl
                LEFT JOIN turns t
                  ON t.speaker_id = pl.id OR t.guesser_id = pl.id
                GROUP BY pl.id, pl.nickname
            ),
            ranked AS (
                SELECT id, score, rounds_played,
                       RANK() OVER (ORDER BY score DESC, nickname ASC) AS rank
                FROM scores
            )
            SELECT score, rounds_played, rank
            FROM ranked
            WHERE id = $1
            """,
            player_id,
            cfg.points_per_validation,
        )
        if row is None:
            return PlayerStats()
        return PlayerStats(
            score=int(row["score"]),
            rounds_played=int(row["rounds_played"]),
            rank=int(row["rank"]),
        )

    async def leaderboard(self, *, top: int) -> list[LeaderboardRow]:
        """Return top nicknames by validated-pair points."""
        logger.info("PostgresGameStore.leaderboard called top=%s", top)
        cfg = get_game_config()
        rows = await self._pool.fetch(
            """
            SELECT pl.nickname,
                   COALESCE(SUM(
                       CASE WHEN t.outcome = 'validated' THEN $1 ELSE 0 END
                   ), 0)::int AS score
            FROM players pl
            LEFT JOIN turns t
              ON t.speaker_id = pl.id OR t.guesser_id = pl.id
            GROUP BY pl.id, pl.nickname
            ORDER BY score DESC, pl.nickname ASC
            LIMIT $2
            """,
            cfg.points_per_validation,
            top,
        )
        return [LeaderboardRow(nickname=r["nickname"], score=int(r["score"])) for r in rows]

    async def metrics(self) -> MetricsSnapshot:
        """Return venue throughput metrics from canonical tables only.

        Definitions match ``MetricsSnapshot`` / frozen ``MetricsResponse``:
        validated turns, eligible records, speaker native langs on validated
        turns, gauntlet pass rate, live-deck generation metrics, and cost/sample
        from complete latest-live-deck totals plus successful
        ``gauntlet_triage`` API-call instrumentation. Other API operations are
        excluded to prevent double-counting deck costs. ``common_langs`` and
        unplayed registrations are absent by query design; native language tags
        are never excluded merely because they may also be bridge languages.
        Mutable ``metrics_counters`` are never read as truth.

        Returns:
            Canonical ``MetricsSnapshot`` for ``/api/metrics``.

        Side effects:
            One read-only aggregate query; INFO logs safe counts only.
        """
        logger.info("PostgresGameStore.metrics called")
        row = await self._pool.fetchrow(
            """
            SELECT
                (SELECT COUNT(*)::int FROM turns WHERE outcome = 'validated')
                    AS validated_pairs,
                (SELECT COUNT(*)::int FROM records WHERE training_eligible IS TRUE)
                    AS training_eligible_pairs,
                (
                    SELECT COUNT(*)::int
                    FROM records r
                    INNER JOIN turns t ON t.id = r.turn_id
                    WHERE t.outcome = 'validated'
                ) AS packaged_validated_records,
                (
                    SELECT COALESCE(json_agg(lang ORDER BY lang), '[]'::json)
                    FROM (
                        SELECT DISTINCT lower(trim(p.native_lang)) AS lang
                        FROM turns t
                        INNER JOIN players p ON p.id = t.speaker_id
                        WHERE t.outcome = 'validated'
                          AND nullif(trim(p.native_lang), '') IS NOT NULL
                    ) speaker_langs
                ) AS languages,
                (
                    SELECT COALESCE(SUM(estimated_cost_microusd), 0)::bigint
                    FROM api_calls
                    WHERE operation = 'gauntlet_triage'
                      AND status = 'success'
                      AND estimated_cost_microusd IS NOT NULL
                ) AS gauntlet_triage_cost_microusd_sum,
                (
                    SELECT COUNT(*)::int
                    FROM api_calls
                    WHERE operation = 'gauntlet_triage'
                      AND status = 'success'
                ) AS successful_gauntlet_triage_call_count,
                (
                    SELECT COUNT(*)::int
                    FROM api_calls
                    WHERE operation = 'gauntlet_triage'
                      AND status = 'success'
                      AND estimated_cost_microusd IS NULL
                ) AS unpriced_gauntlet_triage_call_count,
                (
                    SELECT generation_metrics
                    FROM decks
                    WHERE status = 'live'
                    ORDER BY activated_at DESC NULLS LAST, created_at DESC
                    LIMIT 1
                ) AS generation_metrics
            """
        )
        assert row is not None
        languages = row["languages"]
        if isinstance(languages, str):
            languages = json.loads(languages)
        generation_metrics = row["generation_metrics"]
        if isinstance(generation_metrics, str):
            generation_metrics = json.loads(generation_metrics)
        if generation_metrics is not None and not isinstance(generation_metrics, dict):
            generation_metrics = None
        snapshot = metrics_snapshot_from_aggregates(
            MetricsAggregateRow(
                validated_pairs=int(row["validated_pairs"] or 0),
                training_eligible_pairs=int(row["training_eligible_pairs"] or 0),
                packaged_validated_records=int(row["packaged_validated_records"] or 0),
                languages=[str(x) for x in (languages or [])],
                gauntlet_triage_cost_microusd_sum=int(
                    row["gauntlet_triage_cost_microusd_sum"] or 0
                ),
                successful_gauntlet_triage_call_count=int(
                    row["successful_gauntlet_triage_call_count"] or 0
                ),
                unpriced_gauntlet_triage_call_count=int(
                    row["unpriced_gauntlet_triage_call_count"] or 0
                ),
                generation_metrics=generation_metrics,
            )
        )
        logger.info(
            "PostgresGameStore.metrics completed validated_pairs=%s "
            "training_eligible_pairs=%s language_count=%s "
            "gauntlet_pass_rate_present=%s cost_present=%s "
            "deck_ipm_present=%s",
            snapshot.validated_pairs,
            snapshot.training_eligible_pairs,
            snapshot.language_count,
            snapshot.gauntlet_pass_rate is not None,
            snapshot.cost_per_validated_sample_usd is not None,
            snapshot.deck_images_per_minute is not None,
        )
        return snapshot

    async def count_jobs(self, *, kind: str, turn_id: UUID) -> int:
        """Count jobs for a turn/kind."""
        logger.info(
            "PostgresGameStore.count_jobs called kind=%s turn_id=%s",
            kind,
            turn_id,
        )
        value = await self._pool.fetchval(
            """
            SELECT COUNT(*)::int
            FROM jobs
            WHERE kind = $1 AND payload->>'turn_id' = $2
            """,
            kind,
            str(turn_id),
        )
        return int(value or 0)

    async def seed_deck(
        self,
        *,
        region_tag: str,
        cards: list[dict[str, Any]],
    ) -> UUID:
        """Insert a live deck and cards (test/ops helper)."""
        logger.info(
            "PostgresGameStore.seed_deck called region_tag=%s card_count=%s",
            region_tag,
            len(cards),
        )
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                deck_id = await conn.fetchval(
                    """
                    INSERT INTO decks (region_tag, status)
                    VALUES ($1, 'live')
                    RETURNING id
                    """,
                    region_tag,
                )
                created: list[UUID] = []
                for raw in cards:
                    card_id = await conn.fetchval(
                        """
                        INSERT INTO cards (
                            deck_id, image_path, label_common, decoys, verified
                        )
                        VALUES ($1, $2, $3::jsonb, '[]'::jsonb, TRUE)
                        RETURNING id
                        """,
                        deck_id,
                        str(raw["image_path"]),
                        json.dumps(raw["label_common"]),
                    )
                    created.append(card_id)
                for index, card_id in enumerate(created):
                    decoys = cards[index].get("decoys")
                    if decoys is None:
                        decoy_ids = [str(cid) for i, cid in enumerate(created) if i != index][:5]
                    else:
                        decoy_ids = [str(x) for x in decoys]
                    await conn.execute(
                        """
                        UPDATE cards
                        SET decoys = $2::jsonb
                        WHERE id = $1
                        """,
                        card_id,
                        json.dumps(decoy_ids),
                    )
                return deck_id


def _parse_dt(value: Any) -> datetime:
    """Parse a datetime from JSON or pass through asyncpg values.

    Args:
        value: ISO string or datetime.

    Returns:
        ``datetime`` instance.
    """
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _turn_from_json(raw: Any) -> TurnRecord | None:
    """Build a ``TurnRecord`` from ``row_to_json`` output.

    Args:
        raw: Mapping, JSON string, or ``None``.

    Returns:
        Parsed turn or ``None``.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = json.loads(raw)
    quality = raw.get("quality")
    if isinstance(quality, str):
        quality = json.loads(quality)
    duration = raw.get("duration_s")
    return TurnRecord(
        id=UUID(str(raw["id"])),
        pair_id=UUID(str(raw["pair_id"])),
        speaker_id=UUID(str(raw["speaker_id"])),
        guesser_id=UUID(str(raw["guesser_id"])),
        card_id=UUID(str(raw["card_id"])),
        status=raw["status"],
        audio_path=raw.get("audio_path"),
        audio_flac_path=raw.get("audio_flac_path"),
        duration_s=float(duration) if duration is not None else None,
        quality=dict(quality) if quality is not None else None,
        attempts=int(raw.get("attempts") or 0),
        outcome=raw.get("outcome") or "pending",
        created_at=_parse_dt(raw["created_at"]),
    )
