"""Gauntlet orchestration for durable triage and canonical record packaging.

The service contains the worker's state transitions while adapters own database,
filesystem, and GenAI effects. This preserves the ``triage → package`` split:
triage never exposes labels or creates records, and package never appends an
unscored or ineligible record.

Resilience behaviors owned here:

- stale processing-claim recovery at startup and on a configured interval
- crash-idempotent eligible export (reconcile ``shard_file IS NULL``)
- advisory-locked logical shard flushing with utterance de-dupe before append
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from app.models import GEMINI_FLASH
from worker.config import GauntletLimits
from worker.corpus import CorpusWriter
from worker.media import audio_fingerprint, transcode_to_flac
from worker.models import Job, TriageClient, TriageResult, TurnContext
from worker.prompts import TRIAGE_PROMPT, TRIAGE_RESPONSE_SCHEMA
from worker.repository import GauntletRepository

logger = logging.getLogger(__name__)


class GauntletService:
    """Run claimed gauntlet jobs with injectable GenAI and storage boundaries."""

    def __init__(
        self,
        repository: GauntletRepository,
        triage_client: TriageClient,
        data_dir: Path,
        limits: GauntletLimits,
        *,
        worker_id: str = "test-worker",
        process_id: int | None = None,
        heartbeat_interval_seconds: float = 10.0,
        heartbeat_metadata: dict[str, object] | None = None,
    ) -> None:
        """Set up worker collaborators.

        Args:
            repository: Durable Postgres adapter.
            triage_client: Shared-client adapter implementing ``TriageClient``.
            data_dir: Root of contract-relative runtime audio and corpus paths.
            limits: Centralized operational thresholds and retry configuration.
            worker_id: Stable database heartbeat identity.
            process_id: Current process ID, defaulting to this Python process.
            heartbeat_interval_seconds: Maximum delay between running heartbeats.
            heartbeat_metadata: Redacted operational metadata persisted per beat.
        """
        self._repository = repository
        self._triage_client = triage_client
        self._data_dir = data_dir
        self._limits = limits
        self._worker_id = worker_id
        self._process_id = process_id if process_id is not None else os.getpid()
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._heartbeat_metadata = heartbeat_metadata or {}
        self._corpus = CorpusWriter(data_dir / "corpus", limits.shard_record_limit)
        logger.info(
            "GauntletService initialized data_dir=%s worker_id=%s process_id=%s "
            "heartbeat_interval_seconds=%s",
            data_dir,
            worker_id,
            self._process_id,
            heartbeat_interval_seconds,
        )

    async def recover_stale_claims(self) -> int:
        """Invoke repository stale-claim recovery using centralized limits.

        Returns:
            Number of jobs returned to pending.
        """
        logger.info(
            "GauntletService.recover_stale_claims called stale_after_seconds=%s",
            self._limits.stale_claim_seconds,
        )
        recovered = await self._repository.recover_stale_claims(self._limits.stale_claim_seconds)
        logger.info("GauntletService.recover_stale_claims completed recovered=%s", recovered)
        return recovered

    async def process_once(self) -> bool:
        """Claim and process at most one durable job.

        Returns:
            ``True`` when work was claimed, otherwise ``False`` for an idle poll.
        """
        logger.info("process_once called")
        job = await self._repository.claim_next_job()
        if job is None:
            return False
        try:
            if job.kind == "triage":
                await self._triage(job)
            elif job.kind == "package":
                await self._package(job)
            else:
                raise ValueError(f"unsupported job kind: {job.kind}")
            await self._repository.complete_job(job.id)
        except Exception as error:
            parked = await self._repository.fail_job(
                job,
                _safe_error(error),
                self._limits.retry_base_seconds * (2 ** max(job.tries - 1, 0)),
                self._limits.max_tries,
            )
            logger.warning("job failed job_id=%s kind=%s parked=%s", job.id, job.kind, parked)
        return True

    async def run_forever(self) -> None:
        """Poll jobs with independent periodic recovery and running heartbeats."""
        logger.info(
            "run_forever called poll_seconds=%s stale_claim_interval_seconds=%s "
            "heartbeat_interval_seconds=%s",
            self._limits.poll_seconds,
            self._limits.stale_claim_interval_seconds,
            self._heartbeat_interval_seconds,
        )
        last_recovery = time.monotonic()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name=f"gauntlet-heartbeat-{self._worker_id}",
        )
        try:
            while True:
                now = time.monotonic()
                if now - last_recovery >= self._limits.stale_claim_interval_seconds:
                    await self.recover_stale_claims()
                    last_recovery = time.monotonic()
                worked = await self.process_once()
                if not worked:
                    await asyncio.sleep(self._limits.poll_seconds)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                logger.info("heartbeat loop cancelled worker_id=%s", self._worker_id)

    async def _heartbeat_loop(self) -> None:
        """Publish running status periodically independent of job activity."""
        logger.info(
            "_heartbeat_loop called worker_id=%s interval_seconds=%s",
            self._worker_id,
            self._heartbeat_interval_seconds,
        )
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            try:
                await self._repository.upsert_worker_heartbeat(
                    worker_id=self._worker_id,
                    process_id=self._process_id,
                    status="running",
                    metadata=self._heartbeat_metadata,
                )
            except Exception as error:
                logger.exception(
                    "heartbeat update failed worker_id=%s error_type=%s",
                    self._worker_id,
                    type(error).__name__,
                )

    async def _triage(self, job: Job) -> None:
        """Normalize audio, call the combined model gate, and persist quality.

        Args:
            job: Claimed triage job whose payload identifies one turn.
        """
        logger.info("GauntletService._triage called job_id=%s turn_id=%s", job.id, job.turn_id)
        context = await self._required_context(job.turn_id)
        raw_path = self._data_dir / context.audio_path
        flac_relative = Path("audio") / f"{context.turn_id}.flac"
        flac_path = self._data_dir / flac_relative
        await transcode_to_flac(raw_path, flac_path, self._limits)
        fingerprint = await audio_fingerprint(flac_path, self._limits)
        # Read-only soft hint for the model path; persist_triage owns registration.
        soft_duplicate = await self._repository.speaker_has_matching_fingerprint(
            context.speaker_id, fingerprint, exclude_turn_id=context.turn_id
        )
        prompt = _triage_prompt(context)
        logger.info(
            "GemAI triage request model=%s thinking_level=low prompt=%s response_schema_keys=%s "
            "audio_path=%s audio_bytes=%s",
            GEMINI_FLASH,
            prompt,
            sorted(TRIAGE_RESPONSE_SCHEMA["properties"]),
            flac_path,
            flac_path.stat().st_size,
        )
        response = await self._triage_client.triage_audio(
            model=GEMINI_FLASH,
            prompt=prompt,
            response_schema=TRIAGE_RESPONSE_SCHEMA,
            audio_path=flac_path,
            thinking_level="low",
        )
        logger.info(
            "GemAI triage response model=%s turn_id=%s response=%s",
            GEMINI_FLASH,
            context.turn_id,
            response,
        )
        result = _parse_triage(response, fingerprint.dedup_hash, soft_duplicate)
        await self._repository.persist_triage(
            context.turn_id,
            flac_relative.as_posix(),
            result.as_quality_json(),
            speaker_id=context.speaker_id,
            fingerprint=fingerprint,
        )
        await self._repository.increment_metric("gauntlet_triaged_total")

    async def _package(self, job: Job) -> None:
        """Create a canonical record and reconcile eligible shard export.

        Existing records with ``shard_file IS NULL`` are reconciled rather than
        skipped. Ineligible records are never appended to training shards.

        Args:
            job: Claimed package job whose payload identifies one scored turn.
        """
        logger.info("GauntletService._package called job_id=%s turn_id=%s", job.id, job.turn_id)
        context = await self._required_context(job.turn_id)
        if context.status != "scored" or context.quality is None:
            logger.info(
                "package deferred turn_id=%s status=%s quality_present=%s",
                context.turn_id,
                context.status,
                context.quality is not None,
            )
            return
        golden = _golden_record(context)
        eligible = _training_eligible(context)
        existing = await self._repository.get_record_export_state(context.turn_id)
        if existing is None:
            inserted = await self._repository.create_record(context.turn_id, golden, eligible)
            if inserted:
                await self._repository.increment_metric("gauntlet_records_total")
            existing = await self._repository.get_record_export_state(context.turn_id)
            if existing is None:
                raise RuntimeError(f"record missing after create turn_id={context.turn_id}")
        else:
            logger.info(
                "package reconciling existing_record turn_id=%s eligible=%s shard_file=%s",
                context.turn_id,
                existing.training_eligible,
                existing.shard_file,
            )

        if not existing.training_eligible or not eligible:
            logger.info(
                "package skipping shard export turn_id=%s stored_eligible=%s recomputed_eligible=%s",
                context.turn_id,
                existing.training_eligible,
                eligible,
            )
            return

        if existing.shard_file:
            logger.info(
                "package already linked turn_id=%s shard_file=%s",
                context.turn_id,
                existing.shard_file,
            )
            return

        await self._export_eligible_record(context.turn_id, existing.golden or golden)

    async def _export_eligible_record(self, turn_id: str, golden: dict[str, object]) -> None:
        """Append-or-link one eligible record under the logical flusher lock.

        Args:
            turn_id: Eligible turn UUID.
            golden: Canonical record JSON to append when not already present.
        """
        logger.info("_export_eligible_record called turn_id=%s", turn_id)
        async with self._repository.shard_flusher_lock():
            state = await self._repository.get_record_export_state(turn_id)
            if state is None:
                raise RuntimeError(f"eligible record disappeared turn_id={turn_id}")
            if not state.training_eligible:
                logger.info("_export_eligible_record aborted ineligible turn_id=%s", turn_id)
                return
            if state.shard_file:
                logger.info(
                    "_export_eligible_record already linked turn_id=%s shard=%s",
                    turn_id,
                    state.shard_file,
                )
                return
            found = self._corpus.find_utterance(turn_id)
            if found is not None:
                logger.info(
                    "_export_eligible_record relinking crash_window turn_id=%s shard=%s",
                    turn_id,
                    found,
                )
                await self._repository.set_record_shard(turn_id, found)
                return
            shard = self._corpus.append(golden)
            await self._repository.set_record_shard(turn_id, shard)
            await self._repository.increment_metric("gauntlet_training_eligible_total")
            logger.info("_export_eligible_record completed turn_id=%s shard=%s", turn_id, shard)

    async def _required_context(self, turn_id: str) -> TurnContext:
        """Load a turn context or raise a retryable diagnostic error."""
        logger.info("GauntletService._required_context called turn_id=%s", turn_id)
        context = await self._repository.get_turn_context(turn_id)
        if context is None:
            raise LookupError(f"turn not found: {turn_id}")
        return context


def _triage_prompt(context: TurnContext) -> str:
    """Render the named combined-triage prompt with non-secret turn metadata."""
    logger.info("_triage_prompt called turn_id=%s", context.turn_id)
    label_en = context.label_common.get("en", next(iter(context.label_common.values()), "unknown"))
    translations = ", ".join(f"{key}: {value}" for key, value in context.label_common.items())
    return TRIAGE_PROMPT.format(
        label_en=label_en,
        declared_native_lang=context.native_lang,
        common_langs=", ".join(context.common_langs) or "none declared",
        label_translations=translations,
    )


def _parse_triage(
    response: dict[str, object], fingerprint: str, duplicate: bool
) -> TriageResult:
    """Validate a structured model response and combine it with local checks."""
    logger.info("_parse_triage called response_keys=%s duplicate=%s", sorted(response), duplicate)
    required = set(TRIAGE_RESPONSE_SCHEMA["required"])
    missing = required - set(response)
    if missing:
        raise ValueError(f"triage response missing fields: {sorted(missing)}")
    return TriageResult(
        is_speech=_bool(response, "is_speech"),
        single_speaker=_bool(response, "single_speaker"),
        audio_quality_ok=_bool(response, "audio_quality_ok"),
        contamination_flag=_bool(response, "is_label_readout"),
        apparent_language_note=_string(response, "apparent_language_note"),
        duration_s=float(response["duration_estimate_s"]),
        confidence=float(response["confidence"]),
        dedup_hash=fingerprint,
        duplicate=duplicate,
        readout_reasoning=_string(response, "readout_reasoning"),
    )


def _training_eligible(context: TurnContext) -> bool:
    """Compute eligibility solely from the frozen golden-record gate fields."""
    logger.info("_training_eligible called turn_id=%s", context.turn_id)
    quality = context.quality or {}
    return bool(
        quality.get("is_speech")
        and quality.get("single_speaker")
        and quality.get("audio_quality_ok")
        and not quality.get("contamination_flag")
        and context.outcome == "validated"
        and not quality.get("duplicate")
    )


def _golden_record(context: TurnContext) -> dict[str, object]:
    """Build the exact contract-shaped canonical record for one scored turn."""
    logger.info("_golden_record called turn_id=%s", context.turn_id)
    quality = context.quality or {}
    return {
        "utterance_id": context.turn_id,
        "audio_ref": {"raw_webm": context.audio_path, "clean_flac": context.audio_flac_path},
        "native_lang_tag": context.native_lang,
        "common_lang_text": context.label_common.get(
            context.common_lang, context.label_common.get("en", next(iter(context.label_common.values())))
        ),
        "image_id": context.card_id,
        "deck_id": context.deck_id,
        "validation": {
            "guesser_id": context.guesser_id,
            "correct": context.outcome == "validated",
            "attempts": context.attempts,
        },
        "quality": {
            "is_speech": bool(quality.get("is_speech")),
            "single_speaker": bool(quality.get("single_speaker")),
            "audio_quality_ok": bool(quality.get("audio_quality_ok")),
            "duration_s": quality.get("duration_s", context.duration_s),
            "dedup_hash": quality.get("dedup_hash"),
            "duplicate": bool(quality.get("duplicate")),
            "contamination_flag": bool(quality.get("contamination_flag")),
            "apparent_language_note": quality.get("apparent_language_note", "unsure"),
        },
        "speaker_meta": {
            "player_id": context.speaker_id,
            "declared_region": None,
            "session_id": context.pair_id,
        },
        "timestamps": {
            "captured_at": context.captured_at,
            "packaged_at": datetime.now(UTC).isoformat(),
        },
    }


def _bool(response: dict[str, object], key: str) -> bool:
    """Return a response boolean or reject non-boolean structured output."""
    value = response[key]
    if not isinstance(value, bool):
        raise ValueError(f"triage field {key} must be boolean")
    return value


def _string(response: dict[str, object], key: str) -> str:
    """Return a response string or reject malformed structured output."""
    value = response[key]
    if not isinstance(value, str):
        raise ValueError(f"triage field {key} must be string")
    return value


def _safe_error(error: Exception) -> str:
    """Produce a bounded diagnostic that never includes audio or credentials."""
    logger.info("_safe_error called error_type=%s", type(error).__name__)
    message = str(error).replace("\\", "/")
    return f"{type(error).__name__}: {message[:400]}"
