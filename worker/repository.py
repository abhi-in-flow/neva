"""Asyncpg persistence adapter for durable gauntlet jobs and records.

This module is the only worker database boundary. It claims jobs using an
atomic ``FOR UPDATE SKIP LOCKED`` statement that respects delayed retry
timestamps, recovers stale processing claims, persists idempotent state
changes, serializes logical shard flushing via a Postgres advisory lock, and
registers per-speaker fingerprints through the required Wave 2 schema.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, AsyncIterator

import asyncpg

from worker.fingerprint import (
    AcousticFingerprint,
    decode_envelope,
    encode_envelope,
    fingerprints_match,
    stable_lock_key,
)
from worker.models import Job, TurnContext

logger = logging.getLogger(__name__)

_SPEAKER_FINGERPRINT_TABLE = "speaker_audio_fingerprints"


@dataclass(frozen=True, slots=True)
class RecordExportState:
    """Export linkage state for crash-safe package reconciliation.

    Attributes:
        turn_id: Packaged turn UUID.
        training_eligible: Stored eligibility flag.
        shard_file: Linked shard name, or ``None`` when still unexported.
        golden: Canonical golden-record JSON.
    """

    turn_id: str
    training_eligible: bool
    shard_file: str | None
    golden: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WorkerHeartbeatHealth:
    """Database liveness snapshot for one configured worker.

    Attributes:
        worker_id: Configured stable worker identity.
        exists: Whether a heartbeat row exists.
        status: Last lifecycle status, if present.
        heartbeat_at: Most recent heartbeat timestamp, if present.
        healthy: Whether status is running and timestamp is within threshold.
    """

    worker_id: str
    exists: bool
    status: str | None
    heartbeat_at: datetime | None
    healthy: bool


class GauntletRepository:
    """Persist and retrieve gauntlet state through an asyncpg pool."""

    def __init__(
        self,
        pool: asyncpg.Pool[asyncpg.Record],
        *,
        shard_flusher_lock_id: int,
        fingerprint_max_shift_frames: int = 6,
        fingerprint_near_distance_ratio: float = 0.12,
    ) -> None:
        """Store the externally managed connection pool and lock settings.

        Args:
            pool: Open asyncpg pool targeting the canonical application DB.
            shard_flusher_lock_id: Advisory lock id for logical shard flushing.
            fingerprint_max_shift_frames: Envelope shift radius for near-dup.
            fingerprint_near_distance_ratio: Envelope distance budget.
        """
        self._pool = pool
        self._shard_flusher_lock_id = shard_flusher_lock_id
        self._fingerprint_max_shift_frames = fingerprint_max_shift_frames
        self._fingerprint_near_distance_ratio = fingerprint_near_distance_ratio

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
        recovered = int(result.rsplit(" ", 1)[-1])
        logger.info("recover_stale_claims completed recovered=%s", recovered)
        return recovered

    async def claim_next_job(self) -> Job | None:
        """Atomically claim the oldest due pending job without blocking peers.

        Jobs deferred by retry backoff store a future ``available_at``; rows
        remain pending but are not claimable until that due time.

        Returns:
            A claimed job or ``None`` when no due pending jobs exist.
        """
        logger.info("claim_next_job called")
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                WITH next_job AS (
                    SELECT id FROM jobs
                    WHERE status = 'pending'
                      AND available_at <= now()
                    ORDER BY available_at, created_at
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
        job = Job(id=row["id"], kind=row["kind"], turn_id=row["turn_id"], tries=row["tries"])
        logger.info("claim_next_job claimed job_id=%s kind=%s", job.id, job.kind)
        return job

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
            common_langs=[
                str(value)
                for value in _json_list(row["common_langs"], field="common_langs")
            ],
            common_lang=row["common_lang"],
            card_id=row["card_id"],
            deck_id=row["deck_id"],
            label_common={
                str(key): str(value)
                for key, value in _json_object(
                    row["label_common"],
                    field="label_common",
                ).items()
            },
            audio_path=row["audio_path"],
            audio_flac_path=row["audio_flac_path"],
            duration_s=float(row["duration_s"]) if row["duration_s"] is not None else None,
            quality=(
                _json_object(row["quality"], field="quality")
                if row["quality"] is not None
                else None
            ),
            status=row["status"],
            outcome=row["outcome"],
            attempts=row["attempts"],
            captured_at=row["created_at"].isoformat(),
        )

    async def register_speaker_fingerprint(
        self,
        speaker_id: str,
        fingerprint: AcousticFingerprint,
        turn_id: str,
    ) -> bool:
        """Atomically claim a fingerprint for a speaker when possible.

        Prefer ``persist_triage`` so ownership and the quality write share one
        transaction. Standalone calls remain available for tests.

        Args:
            speaker_id: Speaker UUID scoping de-duplication.
            fingerprint: Fresh acoustic fingerprint for the current turn.
            turn_id: Current turn UUID excluded from prior matches.

        Returns:
            ``True`` when this turn owns the fingerprint (not a duplicate).
        """
        logger.info(
            "register_speaker_fingerprint called speaker_id=%s turn_id=%s hash_prefix=%s",
            speaker_id,
            turn_id,
            fingerprint.content_hash[:12],
        )
        async with self._pool.acquire() as connection, connection.transaction():
            return await self._register_speaker_fingerprint_on_connection(
                connection, speaker_id, fingerprint, turn_id
            )

    async def speaker_has_fingerprint(self, speaker_id: str, fingerprint: str) -> bool:
        """Check whether a prior triaged turn from this speaker has a hash.

        Args:
            speaker_id: Speaker UUID to scope duplicate detection.
            fingerprint: Deterministic content hash, not raw audio.

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

    async def speaker_has_matching_fingerprint(
        self,
        speaker_id: str,
        fingerprint: AcousticFingerprint,
        *,
        exclude_turn_id: str,
    ) -> bool:
        """Read-only duplicate probe used before the slow GenAI triage call.

        Args:
            speaker_id: Speaker UUID to scope duplicate detection.
            fingerprint: Fresh acoustic fingerprint.
            exclude_turn_id: Current turn excluded from matches.

        Returns:
            ``True`` when a prior matching fingerprint is already stored.
        """
        logger.info(
            "speaker_has_matching_fingerprint called speaker_id=%s exclude_turn_id=%s "
            "hash_prefix=%s",
            speaker_id,
            exclude_turn_id,
            fingerprint.content_hash[:12],
        )
        async with self._pool.acquire() as connection:
            owner = await connection.fetchval(
                f"""
                SELECT turn_id::text FROM {_SPEAKER_FINGERPRINT_TABLE}
                WHERE speaker_id = $1::uuid AND fingerprint = $2
                """,
                speaker_id,
                fingerprint.content_hash,
            )
            if owner is not None and owner != exclude_turn_id:
                return True
            return not await self._scan_fingerprint_unowned(
                speaker_id,
                fingerprint,
                exclude_turn_id,
                connection=connection,
            )

    async def persist_triage(
        self,
        turn_id: str,
        flac_path: str,
        quality: dict[str, object],
        *,
        speaker_id: str | None = None,
        fingerprint: AcousticFingerprint | None = None,
    ) -> dict[str, object]:
        """Store machine quality and conditionally enqueue packaging.

        When ``fingerprint`` is provided, duplicate ownership is finalized in the
        same transaction as the quality UPDATE so concurrent equal samples cannot
        both observe an empty prior set.

        Args:
            turn_id: Triaged turn UUID.
            flac_path: Contract-relative clean FLAC path.
            quality: Schema-compatible quality metadata.
            speaker_id: Speaker used for duplicate finalization.
            fingerprint: Acoustic fingerprint for atomic registration.

        Returns:
            The quality object actually persisted (duplicate flag may change).
        """
        logger.info(
            "persist_triage called turn_id=%s flac_path=%s quality_keys=%s fingerprint=%s",
            turn_id,
            flac_path,
            sorted(quality),
            fingerprint.content_hash[:12] if fingerprint is not None else None,
        )
        persisted = dict(quality)
        async with self._pool.acquire() as connection, connection.transaction():
            if speaker_id is not None and fingerprint is not None:
                owned = await self._register_speaker_fingerprint_on_connection(
                    connection, speaker_id, fingerprint, turn_id
                )
                persisted["duplicate"] = not owned
                persisted["dedup_hash"] = fingerprint.dedup_hash
                persisted["acoustic_envelope"] = encode_envelope(fingerprint.envelope)
            await connection.execute(
                "UPDATE turns SET audio_flac_path = $2, quality = $3::jsonb WHERE id = $1::uuid",
                turn_id,
                flac_path,
                json.dumps(persisted),
            )
            await connection.execute(
                """
                INSERT INTO jobs (kind, payload, status)
                SELECT 'package', jsonb_build_object('turn_id', id::text), 'pending'
                FROM turns
                WHERE id = $1::uuid
                  AND status = 'scored'
                  AND quality IS NOT NULL
                ON CONFLICT DO NOTHING
                """,
                turn_id,
            )
        return persisted

    async def get_record_export_state(self, turn_id: str) -> RecordExportState | None:
        """Load packaging/export linkage for crash reconciliation.

        Args:
            turn_id: Packaged turn UUID.

        Returns:
            Export state or ``None`` when no record exists yet.
        """
        logger.info("get_record_export_state called turn_id=%s", turn_id)
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT turn_id::text, training_eligible, shard_file, golden
                FROM records
                WHERE turn_id = $1::uuid
                """,
                turn_id,
            )
        if row is None:
            return None
        return RecordExportState(
            turn_id=row["turn_id"],
            training_eligible=bool(row["training_eligible"]),
            shard_file=row["shard_file"],
            golden=_json_object(row["golden"], field="golden"),
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
                """
                UPDATE records
                SET shard_file = $2
                WHERE turn_id = $1::uuid
                  AND training_eligible = TRUE
                  AND (shard_file IS NULL OR shard_file = $2)
                """,
                turn_id,
                shard_file,
            )

    @asynccontextmanager
    async def shard_flusher_lock(self) -> AsyncIterator[None]:
        """Serialize logical shard flush/link work across worker processes.

        Yields:
            ``None`` after the session advisory lock is held. The lock is always
            released when the context exits, including on exceptions.
        """
        logger.info(
            "shard_flusher_lock acquiring lock_id=%s",
            self._shard_flusher_lock_id,
        )
        connection = await self._pool.acquire()
        try:
            await connection.execute(
                "SELECT pg_advisory_lock($1)",
                self._shard_flusher_lock_id,
            )
            logger.info("shard_flusher_lock acquired lock_id=%s", self._shard_flusher_lock_id)
            try:
                yield
            finally:
                await connection.execute(
                    "SELECT pg_advisory_unlock($1)",
                    self._shard_flusher_lock_id,
                )
                logger.info("shard_flusher_lock released lock_id=%s", self._shard_flusher_lock_id)
        finally:
            await self._pool.release(connection)

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

    async def upsert_worker_heartbeat(
        self,
        *,
        worker_id: str,
        process_id: int,
        status: str,
        metadata: dict[str, object],
    ) -> None:
        """Publish worker lifecycle and liveness metadata.

        Args:
            worker_id: Stable configured identity for this worker replica.
            process_id: Current operating-system process ID.
            status: One of ``starting``, ``running``, or ``stopping``.
            metadata: Redacted operational metadata; never secrets or payloads.
        """
        logger.info(
            "upsert_worker_heartbeat called worker_id=%s process_id=%s status=%s "
            "metadata_keys=%s",
            worker_id,
            process_id,
            status,
            sorted(metadata),
        )
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO worker_heartbeats
                    (worker_id, process_id, status, started_at, heartbeat_at, metadata)
                VALUES ($1, $2, $3, now(), now(), $4::jsonb)
                ON CONFLICT (worker_id) DO UPDATE
                SET process_id = EXCLUDED.process_id,
                    status = EXCLUDED.status,
                    started_at = CASE
                        WHEN EXCLUDED.status = 'starting' THEN now()
                        ELSE worker_heartbeats.started_at
                    END,
                    heartbeat_at = now(),
                    metadata = EXCLUDED.metadata
                """,
                worker_id,
                process_id,
                status,
                json.dumps(metadata),
            )

    async def get_worker_heartbeat_health(
        self,
        *,
        worker_id: str,
        stale_after_seconds: float,
    ) -> WorkerHeartbeatHealth:
        """Read whether one worker heartbeat is running and recent.

        Args:
            worker_id: Configured identity expected by the healthcheck.
            stale_after_seconds: Maximum accepted heartbeat age.

        Returns:
            Structured health state. Missing, stale, starting, and stopping rows
            are unhealthy.
        """
        logger.info(
            "get_worker_heartbeat_health called worker_id=%s stale_after_seconds=%s",
            worker_id,
            stale_after_seconds,
        )
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT status, heartbeat_at,
                       status = 'running'
                       AND heartbeat_at >= now() - $2::interval AS healthy
                FROM worker_heartbeats
                WHERE worker_id = $1
                """,
                worker_id,
                timedelta(seconds=stale_after_seconds),
            )
        if row is None:
            return WorkerHeartbeatHealth(
                worker_id=worker_id,
                exists=False,
                status=None,
                heartbeat_at=None,
                healthy=False,
            )
        return WorkerHeartbeatHealth(
            worker_id=worker_id,
            exists=True,
            status=row["status"],
            heartbeat_at=row["heartbeat_at"],
            healthy=bool(row["healthy"]),
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
                    available_at = now() + $3::interval
                WHERE id=$1::uuid
                """,
                job.id,
                error[:500],
                timedelta(seconds=retry_delay_seconds),
            )
        return False

    async def _register_speaker_fingerprint_on_connection(
        self,
        connection: asyncpg.Connection[asyncpg.Record],
        speaker_id: str,
        fingerprint: AcousticFingerprint,
        turn_id: str,
    ) -> bool:
        """Register fingerprint ownership inside the caller's transaction.

        Args:
            connection: Connection already inside a transaction.
            speaker_id: Speaker UUID.
            fingerprint: Candidate fingerprint.
            turn_id: Current turn UUID.

        Returns:
            ``True`` when this turn owns the fingerprint.
        """
        logger.info(
            "_register_speaker_fingerprint_on_connection called speaker_id=%s turn_id=%s",
            speaker_id,
            turn_id,
        )
        # Speaker-scoped lock covers exact and envelope near-duplicates together.
        await connection.execute(
            "SELECT pg_advisory_xact_lock($1)",
            stable_lock_key(speaker_id),
        )
        exact_owned = await self._register_fingerprint_table_on(
            connection, speaker_id, fingerprint, turn_id
        )
        if not exact_owned:
            return False
        near_clear = await self._scan_fingerprint_unowned(
            speaker_id,
            fingerprint,
            turn_id,
            connection=connection,
        )
        if near_clear:
            return True
        await connection.execute(
            f"""
            DELETE FROM {_SPEAKER_FINGERPRINT_TABLE}
            WHERE speaker_id = $1::uuid
              AND fingerprint = $2
              AND turn_id = $3::uuid
            """,
            speaker_id,
            fingerprint.content_hash,
            turn_id,
        )
        return False

    async def _register_fingerprint_table_on(
        self,
        connection: asyncpg.Connection[asyncpg.Record],
        speaker_id: str,
        fingerprint: AcousticFingerprint,
        turn_id: str,
    ) -> bool:
        """Insert into the migration table on an open connection.

        Args:
            connection: Open transactional connection.
            speaker_id: Speaker UUID.
            fingerprint: Content hash to register.
            turn_id: Current turn claiming ownership.

        Returns:
            ``True`` when this turn owns the row after insert or same-turn retry.
        """
        logger.info(
            "_register_fingerprint_table_on called speaker_id=%s turn_id=%s",
            speaker_id,
            turn_id,
        )
        row = await connection.fetchrow(
            f"""
            INSERT INTO {_SPEAKER_FINGERPRINT_TABLE} (speaker_id, fingerprint, turn_id)
            VALUES ($1::uuid, $2, $3::uuid)
            ON CONFLICT DO NOTHING
            RETURNING turn_id::text
            """,
            speaker_id,
            fingerprint.content_hash,
            turn_id,
        )
        if row is not None:
            return True
        owner = await connection.fetchval(
            f"""
            SELECT turn_id::text FROM {_SPEAKER_FINGERPRINT_TABLE}
            WHERE speaker_id = $1::uuid AND fingerprint = $2
            """,
            speaker_id,
            fingerprint.content_hash,
        )
        return owner == turn_id

    async def _scan_fingerprint_unowned(
        self,
        speaker_id: str,
        fingerprint: AcousticFingerprint,
        exclude_turn_id: str,
        *,
        connection: asyncpg.Connection[asyncpg.Record] | None = None,
    ) -> bool:
        """Return ``True`` when no prior matching fingerprint exists.

        Args:
            speaker_id: Speaker UUID.
            fingerprint: Candidate fingerprint.
            exclude_turn_id: Turn excluded from the scan.
            connection: Optional open connection already inside a transaction.

        Returns:
            ``True`` when the candidate appears unowned.
        """
        logger.info(
            "_scan_fingerprint_unowned called speaker_id=%s exclude_turn_id=%s",
            speaker_id,
            exclude_turn_id,
        )

        async def _run(active: asyncpg.Connection[asyncpg.Record]) -> bool:
            """Execute the prior-quality scan on one connection."""
            rows = await active.fetch(
                """
                SELECT id::text AS turn_id, quality
                FROM turns
                WHERE speaker_id = $1::uuid
                  AND id <> $2::uuid
                  AND quality IS NOT NULL
                """,
                speaker_id,
                exclude_turn_id,
            )
            for row in rows:
                quality = _json_object(row["quality"], field="quality")
                prior_hash = quality.get("dedup_hash")
                if prior_hash == fingerprint.content_hash:
                    return False
                encoded = quality.get("acoustic_envelope")
                if isinstance(prior_hash, str) and isinstance(encoded, str) and encoded:
                    prior = AcousticFingerprint(
                        content_hash=prior_hash,
                        envelope=decode_envelope(encoded),
                        frame_ms=fingerprint.frame_ms,
                    )
                    if fingerprints_match(
                        prior,
                        fingerprint,
                        max_shift_frames=self._fingerprint_max_shift_frames,
                        near_distance_ratio=self._fingerprint_near_distance_ratio,
                    ):
                        return False
            return True

        if connection is not None:
            return await _run(connection)
        async with self._pool.acquire() as acquired:
            return await _run(acquired)


def _json_object(value: object, *, field: str) -> dict[str, Any]:
    """Normalize an asyncpg JSON/JSONB value into an object mapping.

    Asyncpg returns JSONB as encoded text unless a custom codec is installed,
    while unit-test fakes often return dictionaries directly.

    Args:
        value: JSON text, bytes, or mapping returned by the database adapter.
        field: Safe field name used only in diagnostics.

    Returns:
        A string-keyed object mapping.

    Raises:
        ValueError: When decoding fails or the JSON value is not an object.
    """
    logger.info("_json_object called field=%s value_type=%s", field, type(value).__name__)
    decoded: object
    if isinstance(value, Mapping):
        decoded = dict(value)
    elif isinstance(value, bytes):
        decoded = json.loads(value.decode("utf-8"))
    elif isinstance(value, str):
        decoded = json.loads(value)
    else:
        raise ValueError(f"{field} must be a JSON object")
    if not isinstance(decoded, dict):
        raise ValueError(f"{field} must decode to a JSON object")
    return {str(key): item for key, item in decoded.items()}


def _json_list(value: object, *, field: str) -> list[Any]:
    """Normalize an asyncpg JSON/JSONB value into a list.

    Args:
        value: JSON text, bytes, or list returned by the database adapter.
        field: Safe field name used only in diagnostics.

    Returns:
        Decoded JSON list.

    Raises:
        ValueError: When decoding fails or the JSON value is not a list.
    """
    logger.info("_json_list called field=%s value_type=%s", field, type(value).__name__)
    decoded: object
    if isinstance(value, list):
        decoded = value
    elif isinstance(value, bytes):
        decoded = json.loads(value.decode("utf-8"))
    elif isinstance(value, str):
        decoded = json.loads(value)
    else:
        raise ValueError(f"{field} must be a JSON list")
    if not isinstance(decoded, list):
        raise ValueError(f"{field} must decode to a JSON list")
    return decoded
