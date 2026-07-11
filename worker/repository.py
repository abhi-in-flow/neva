"""Asyncpg persistence adapter for durable gauntlet jobs and records.

This module is the only worker database boundary. It claims jobs using an
atomic ``FOR UPDATE SKIP LOCKED`` statement, persists idempotent state changes,
and keeps packaging safe across concurrent worker processes and restarts.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import asyncpg

from worker.models import Job, TurnContext

logger = logging.getLogger(__name__)


class GauntletRepository:
    """Persist and retrieve gauntlet state through an asyncpg pool."""

    def __init__(self, pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Store the externally managed connection pool.

        Args:
            pool: Open asyncpg pool targeting the canonical application DB.
        """
        self._pool = pool

    async def recover_stale_claims(self, stale_after_seconds: float) -> int:
        """Return abandoned processing jobs to pending after a worker crash.

        Args:
            stale_after_seconds: Age beyond which a claim is considered dead.

        Returns:
            Number of recovered jobs.
        """
        logger.info("recover_stale_claims called stale_after_seconds=%s", stale_after_seconds)
        interval = timedelta(seconds=stale_after_seconds)
        async with self._pool.acquire() as connection:
            result = await connection.execute(
                """
                UPDATE jobs SET status = 'pending', claimed_at = NULL
                WHERE status = 'processing' AND claimed_at < now() - $1::interval
                """,
                interval,
            )
        return int(result.rsplit(" ", 1)[-1])

    async def claim_next_job(self) -> Job | None:
        """Atomically claim the oldest pending job without blocking peers.

        Returns:
            A claimed job or ``None`` when no pending jobs exist.
        """
        logger.info("claim_next_job called")
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                WITH next_job AS (
                    SELECT id FROM jobs
                    WHERE status = 'pending'
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE jobs AS job
                SET status = 'processing', claimed_at = now(), tries = job.tries + 1
                FROM next_job
                WHERE job.id = next_job.id
                RETURNING job.id::text, job.kind, job.payload->>'turn_id' AS turn_id, job.tries
                """
            )
        if row is None:
            return None
        return Job(id=row["id"], kind=row["kind"], turn_id=row["turn_id"], tries=row["tries"])

    async def get_turn_context(self, turn_id: str) -> TurnContext | None:
        """Load the joined turn, player, pair, card, and deck values.

        Args:
            turn_id: Canonical turn UUID string.

        Returns:
            A complete immutable context or ``None`` if the turn disappeared.
        """
        logger.info("get_turn_context called turn_id=%s", turn_id)
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT t.id::text AS turn_id, t.pair_id::text, t.speaker_id::text,
                       t.guesser_id::text, p.native_lang, p.common_langs, pair.common_lang,
                       c.id::text AS card_id, c.deck_id::text, c.label_common,
                       t.audio_path, t.audio_flac_path, t.duration_s, t.quality,
                       t.status, t.outcome, t.attempts, t.created_at
                FROM turns t
                JOIN players p ON p.id = t.speaker_id
                JOIN cards c ON c.id = t.card_id
                JOIN pairs pair ON pair.id = t.pair_id
                WHERE t.id = $1::uuid
                """,
                turn_id,
            )
        if row is None:
            return None
        return TurnContext(
            turn_id=row["turn_id"],
            pair_id=row["pair_id"],
            speaker_id=row["speaker_id"],
            guesser_id=row["guesser_id"],
            native_lang=row["native_lang"],
            common_langs=list(row["common_langs"]),
            common_lang=row["common_lang"],
            card_id=row["card_id"],
            deck_id=row["deck_id"],
            label_common=dict(row["label_common"]),
            audio_path=row["audio_path"],
            audio_flac_path=row["audio_flac_path"],
            duration_s=float(row["duration_s"]) if row["duration_s"] is not None else None,
            quality=dict(row["quality"]) if row["quality"] is not None else None,
            status=row["status"],
            outcome=row["outcome"],
            attempts=row["attempts"],
            captured_at=row["created_at"].isoformat(),
        )

    async def speaker_has_fingerprint(self, speaker_id: str, fingerprint: str) -> bool:
        """Check whether a prior triaged turn from this speaker has a fingerprint.

        Args:
            speaker_id: Speaker UUID to scope duplicate detection.
            fingerprint: Deterministic file fingerprint, not raw audio.

        Returns:
            Whether an earlier turn already carries the same hash.
        """
        logger.info(
            "speaker_has_fingerprint called speaker_id=%s fingerprint_prefix=%s",
            speaker_id,
            fingerprint[:12],
        )
        async with self._pool.acquire() as connection:
            return bool(
                await connection.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM turns
                        WHERE speaker_id = $1::uuid
                          AND quality->>'dedup_hash' = $2
                    )
                    """,
                    speaker_id,
                    fingerprint,
                )
            )

    async def persist_triage(self, turn_id: str, flac_path: str, quality: dict[str, object]) -> None:
        """Store machine quality and conditionally enqueue packaging.

        Args:
            turn_id: Triaged turn UUID.
            flac_path: Contract-relative clean FLAC path.
            quality: Schema-compatible quality metadata.
        """
        logger.info("persist_triage called turn_id=%s flac_path=%s quality_keys=%s", turn_id, flac_path, sorted(quality))
        async with self._pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "UPDATE turns SET audio_flac_path = $2, quality = $3::jsonb WHERE id = $1::uuid",
                turn_id,
                flac_path,
                json.dumps(quality),
            )
            await connection.execute(
                """
                INSERT INTO jobs (kind, payload)
                SELECT 'package', jsonb_build_object('turn_id', id::text)
                FROM turns WHERE id = $1::uuid AND status = 'scored' AND quality IS NOT NULL
                ON CONFLICT (kind, (payload->>'turn_id')) DO NOTHING
                """,
                turn_id,
            )

    async def create_record(self, turn_id: str, golden: dict[str, object], eligible: bool) -> bool:
        """Create the immutable canonical record if a peer has not already done so.

        Args:
            turn_id: Packaged turn UUID.
            golden: Canonical golden-record JSON.
            eligible: Recomputed training eligibility.

        Returns:
            ``True`` only for the worker that inserted the record.
        """
        logger.info("create_record called turn_id=%s eligible=%s golden_keys=%s", turn_id, eligible, sorted(golden))
        async with self._pool.acquire() as connection:
            result = await connection.execute(
                """
                INSERT INTO records (turn_id, golden, training_eligible)
                VALUES ($1::uuid, $2::jsonb, $3)
                ON CONFLICT (turn_id) DO NOTHING
                """,
                turn_id,
                json.dumps(golden),
                eligible,
            )
        return result.endswith(" 1")

    async def set_record_shard(self, turn_id: str, shard_file: str) -> None:
        """Record the append-only shard location after a successful flush."""
        logger.info("set_record_shard called turn_id=%s shard_file=%s", turn_id, shard_file)
        async with self._pool.acquire() as connection:
            await connection.execute(
                "UPDATE records SET shard_file = $2 WHERE turn_id = $1::uuid",
                turn_id,
                shard_file,
            )

    async def increment_metric(self, key: str) -> None:
        """Atomically increment a named worker throughput metric."""
        logger.info("increment_metric called key=%s", key)
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO metrics_counters (key, value) VALUES ($1, 1)
                ON CONFLICT (key) DO UPDATE
                SET value = metrics_counters.value + 1, updated_at = now()
                """,
                key,
            )

    async def complete_job(self, job_id: str) -> None:
        """Mark a successfully handled job complete."""
        logger.info("complete_job called job_id=%s", job_id)
        async with self._pool.acquire() as connection:
            await connection.execute(
                "UPDATE jobs SET status = 'complete', completed_at = now() WHERE id = $1::uuid",
                job_id,
            )

    async def fail_job(
        self, job: Job, error: str, retry_delay_seconds: float, max_tries: int
    ) -> bool:
        """Retry or park a failed job without exposing unsafe exception details.

        Args:
            job: Failed claimed job.
            error: Sanitized diagnostic string.
            retry_delay_seconds: Exponential delay before its next claim.
            max_tries: Configured attempt count after which it is parked.

        Returns:
            ``True`` when parked permanently, otherwise ``False``.
        """
        logger.info(
            "fail_job called job_id=%s tries=%s delay_seconds=%s",
            job.id,
            job.tries,
            retry_delay_seconds,
        )
        # A pending job keeps the schema simple; deferring created_at prevents it
        # from being reclaimed until backoff has elapsed while retaining SKIP LOCKED.
        async with self._pool.acquire() as connection:
            if job.tries >= max_tries:
                await connection.execute(
                    "UPDATE jobs SET status='failed', last_error=$2, completed_at=now() WHERE id=$1::uuid",
                    job.id,
                    error[:500],
                )
                return True
            await connection.execute(
                """
                UPDATE jobs SET status='pending', claimed_at=NULL, last_error=$2,
                    created_at = now() + $3::interval
                WHERE id=$1::uuid
                """,
                job.id,
                error[:500],
                timedelta(seconds=retry_delay_seconds),
            )
        return False
