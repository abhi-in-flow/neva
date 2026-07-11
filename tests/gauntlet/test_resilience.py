"""Focused resilience tests for stale claims, export recovery, and flusher safety.

These tests stay inside temporary directories and in-memory fakes. They never open
asyncpg pools, call Gemini, or touch the live ``data/`` tree. Repository SQL
contracts that gate retry due-time filtering are asserted from source text so
the claim predicate cannot regress silently.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from worker.config import GauntletLimits
from worker.corpus import CorpusWriter
from worker.models import Job, TurnContext
from worker.repository import GauntletRepository, RecordExportState
from worker.service import GauntletService, _training_eligible


def _context(
    *,
    outcome: str = "validated",
    quality: dict[str, object] | None = None,
    turn_id: str = "00000000-0000-0000-0000-000000000001",
) -> TurnContext:
    """Build an isolated contract-shaped turn snapshot."""
    return TurnContext(
        turn_id=turn_id,
        pair_id="00000000-0000-0000-0000-000000000002",
        speaker_id="00000000-0000-0000-0000-000000000003",
        guesser_id="00000000-0000-0000-0000-000000000004",
        native_lang="as",
        common_langs=["en", "hi"],
        common_lang="hi",
        card_id="00000000-0000-0000-0000-000000000005",
        deck_id="00000000-0000-0000-0000-000000000006",
        label_common={"en": "water pot", "hi": "घड़ा"},
        audio_path=f"audio/{turn_id}.webm",
        audio_flac_path=f"audio/{turn_id}.flac",
        duration_s=3.2,
        quality=quality,
        status="scored",
        outcome=outcome,
        attempts=0,
        captured_at="2026-07-11T06:00:00+00:00",
    )


def _clean_quality(*, duplicate: bool = False) -> dict[str, object]:
    """Return gate-passing quality metadata for packaging fixtures."""
    return {
        "is_speech": True,
        "single_speaker": True,
        "audio_quality_ok": True,
        "duration_s": 3.2,
        "dedup_hash": "a" * 64,
        "duplicate": duplicate,
        "contamination_flag": False,
        "apparent_language_note": "as-like",
    }


class FakeRepository:
    """In-memory repository boundary used by packaging resilience tests."""

    def __init__(self) -> None:
        """Initialize empty durable-looking state."""
        self.recover_calls: list[float] = []
        self.records: dict[str, RecordExportState] = {}
        self.metrics: list[str] = []
        self.completed: list[str] = []
        self.lock_holders = 0
        self.max_lock_holders = 0
        self._lock = asyncio.Lock()
        self.context: TurnContext | None = None
        self.jobs: list[Job] = []
        self.heartbeats: list[tuple[str, int, str, dict[str, object]]] = []

    async def recover_stale_claims(self, stale_after_seconds: float) -> int:
        """Record recovery invocations for startup/interval assertions."""
        self.recover_calls.append(stale_after_seconds)
        return len(self.recover_calls)

    async def claim_next_job(self) -> Job | None:
        """Pop one queued fake job."""
        if not self.jobs:
            return None
        return self.jobs.pop(0)

    async def get_turn_context(self, turn_id: str) -> TurnContext | None:
        """Return the configured turn context when IDs match."""
        if self.context is None or self.context.turn_id != turn_id:
            return None
        return self.context

    async def get_record_export_state(self, turn_id: str) -> RecordExportState | None:
        """Return the in-memory export state."""
        return self.records.get(turn_id)

    async def create_record(self, turn_id: str, golden: dict[str, object], eligible: bool) -> bool:
        """Insert once, mirroring ``ON CONFLICT DO NOTHING``."""
        if turn_id in self.records:
            return False
        self.records[turn_id] = RecordExportState(
            turn_id=turn_id,
            training_eligible=eligible,
            shard_file=None,
            golden=golden,
        )
        return True

    async def set_record_shard(self, turn_id: str, shard_file: str) -> None:
        """Link an eligible record to a shard name."""
        state = self.records[turn_id]
        if not state.training_eligible:
            return
        self.records[turn_id] = RecordExportState(
            turn_id=state.turn_id,
            training_eligible=state.training_eligible,
            shard_file=shard_file,
            golden=state.golden,
        )

    async def increment_metric(self, key: str) -> None:
        """Append a metric key for ordering assertions."""
        self.metrics.append(key)

    async def complete_job(self, job_id: str) -> None:
        """Mark a fake job complete."""
        self.completed.append(job_id)

    async def fail_job(self, job: Job, error: str, retry_delay_seconds: float, max_tries: int) -> bool:
        """Unused failure path for successful packaging fixtures."""
        raise AssertionError(f"unexpected fail_job: {error}")

    async def upsert_worker_heartbeat(
        self,
        *,
        worker_id: str,
        process_id: int,
        status: str,
        metadata: dict[str, object],
    ) -> None:
        """Capture worker heartbeat updates."""
        self.heartbeats.append((worker_id, process_id, status, metadata))

    def shard_flusher_lock(self):
        """Async context manager that tracks concurrent lock holders."""

        repo = self

        class _Lock:
            """Hold the fake advisory lock for one flusher critical section."""

            async def __aenter__(self) -> None:
                """Acquire and track peak concurrency."""
                await repo._lock.acquire()
                repo.lock_holders += 1
                repo.max_lock_holders = max(repo.max_lock_holders, repo.lock_holders)

            async def __aexit__(self, exc_type, exc, tb) -> None:
                """Release the fake advisory lock."""
                repo.lock_holders -= 1
                repo._lock.release()

        return _Lock()


@pytest.mark.asyncio
async def test_stale_claim_recovery_invoked_periodically_without_startup_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Service runs periodic recovery but leaves startup recovery to entrypoint."""
    repo = FakeRepository()
    limits = GauntletLimits(poll_seconds=0.01, stale_claim_interval_seconds=0.05, stale_claim_seconds=90)
    service = GauntletService(repo, AsyncMock(), tmp_path, limits)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "process_once", AsyncMock(return_value=False))
    task = asyncio.create_task(service.run_forever())
    try:
        for _ in range(200):
            if len(repo.recover_calls) >= 1:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail(f"expected periodic recovery, saw calls={repo.recover_calls}")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert repo.recover_calls == [90]


def test_claim_next_job_filters_and_orders_by_available_at() -> None:
    """Retry backoff must use the dedicated due-time column."""
    source = inspect.getsource(GauntletRepository.claim_next_job)
    assert "available_at <= now()" in source
    assert "ORDER BY available_at, created_at" in source
    assert "status = 'pending'" in source


def test_fail_job_updates_available_at_without_mutating_created_at() -> None:
    """Retry scheduling must preserve immutable job creation timestamps."""
    source = inspect.getsource(GauntletRepository.fail_job)
    assert "available_at = now()" in source
    assert "created_at =" not in source


def test_package_enqueue_uses_bare_conflict_safety() -> None:
    """Worker package enqueue must handle all unique conflicts atomically."""
    source = inspect.getsource(GauntletRepository.persist_triage)
    assert "ON CONFLICT DO NOTHING" in source
    assert "NOT EXISTS" not in source
    assert "SELECT 'package'" in source


@pytest.mark.asyncio
async def test_package_reconciles_null_shard_after_insert_crash(tmp_path: Path) -> None:
    """Eligible records with ``shard_file IS NULL`` must export on retry."""
    quality = _clean_quality()
    context = _context(quality=quality)
    repo = FakeRepository()
    repo.context = context
    repo.records[context.turn_id] = RecordExportState(
        turn_id=context.turn_id,
        training_eligible=True,
        shard_file=None,
        golden={"utterance_id": context.turn_id, "quality": quality},
    )
    repo.jobs = [Job(id="job-1", kind="package", turn_id=context.turn_id, tries=1)]
    service = GauntletService(repo, AsyncMock(), tmp_path, GauntletLimits(shard_record_limit=10))  # type: ignore[arg-type]

    worked = await service.process_once()

    assert worked is True
    assert repo.records[context.turn_id].shard_file == "shard_0001.jsonl"
    shard_path = tmp_path / "corpus" / "shard_0001.jsonl"
    assert json.loads(shard_path.read_text(encoding="utf-8"))["utterance_id"] == context.turn_id
    assert "gauntlet_training_eligible_total" in repo.metrics


@pytest.mark.asyncio
async def test_package_relinks_after_append_before_link_crash(tmp_path: Path) -> None:
    """If the JSONL line exists but linkage died, retry must set shard_file only."""
    quality = _clean_quality()
    context = _context(quality=quality)
    golden = {"utterance_id": context.turn_id, "marker": "pre-crash"}
    writer = CorpusWriter(tmp_path / "corpus", shard_record_limit=10)
    shard = writer.append(golden)
    repo = FakeRepository()
    repo.context = context
    repo.records[context.turn_id] = RecordExportState(
        turn_id=context.turn_id,
        training_eligible=True,
        shard_file=None,
        golden=golden,
    )
    repo.jobs = [Job(id="job-2", kind="package", turn_id=context.turn_id, tries=2)]
    service = GauntletService(repo, AsyncMock(), tmp_path, GauntletLimits())  # type: ignore[arg-type]

    await service.process_once()

    assert repo.records[context.turn_id].shard_file == shard
    lines = (tmp_path / "corpus" / shard).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "gauntlet_training_eligible_total" not in repo.metrics


@pytest.mark.asyncio
async def test_ineligible_record_never_enters_training_shard(tmp_path: Path) -> None:
    """Unclear / contaminated / duplicate outcomes must not append JSONL."""
    quality = _clean_quality()
    quality["contamination_flag"] = True
    context = _context(outcome="validated", quality=quality)
    assert _training_eligible(context) is False
    repo = FakeRepository()
    repo.context = context
    repo.jobs = [Job(id="job-3", kind="package", turn_id=context.turn_id, tries=1)]
    service = GauntletService(repo, AsyncMock(), tmp_path, GauntletLimits())  # type: ignore[arg-type]

    await service.process_once()

    state = repo.records[context.turn_id]
    assert state.training_eligible is False
    assert state.shard_file is None
    assert not (tmp_path / "corpus").exists() or not list((tmp_path / "corpus").glob("*.jsonl"))


@pytest.mark.asyncio
async def test_concurrent_logical_flusher_serializes_appends(tmp_path: Path) -> None:
    """Advisory flusher lock must keep peak concurrent holders at one."""
    repo = FakeRepository()
    limits = GauntletLimits(shard_record_limit=100)
    service = GauntletService(repo, AsyncMock(), tmp_path, limits)  # type: ignore[arg-type]
    turn_ids = [f"00000000-0000-0000-0000-00000000000{index}" for index in range(1, 5)]
    for turn_id in turn_ids:
        repo.records[turn_id] = RecordExportState(
            turn_id=turn_id,
            training_eligible=True,
            shard_file=None,
            golden={"utterance_id": turn_id},
        )

    await asyncio.gather(
        *(service._export_eligible_record(turn_id, {"utterance_id": turn_id}) for turn_id in turn_ids)
    )

    assert repo.max_lock_holders == 1
    for turn_id in turn_ids:
        assert repo.records[turn_id].shard_file == "shard_0001.jsonl"
    lines = (tmp_path / "corpus" / "shard_0001.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4
    assert {json.loads(line)["utterance_id"] for line in lines} == set(turn_ids)


def test_corpus_append_is_idempotent_for_existing_utterance(tmp_path: Path) -> None:
    """A second append for the same utterance_id must not duplicate the line."""
    writer = CorpusWriter(tmp_path / "corpus", shard_record_limit=10)
    first = writer.append({"utterance_id": "u-1", "n": 1})
    second = writer.append({"utterance_id": "u-1", "n": 2})
    assert first == second == "shard_0001.jsonl"
    lines = (tmp_path / "corpus" / first).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["n"] == 1


def test_register_speaker_fingerprint_requires_wave2_table() -> None:
    """Repository uses the required atomic registration table without fallback."""
    assert hasattr(GauntletRepository, "register_speaker_fingerprint")
    module_source = inspect.getsource(GauntletRepository)
    registration_source = inspect.getsource(
        GauntletRepository._register_fingerprint_table_on
    )
    assert "_SPEAKER_FINGERPRINT_TABLE" in registration_source
    assert "pg_advisory_xact_lock" in module_source
    assert "_register_speaker_fingerprint_on_connection" in module_source
    assert "information_schema.tables" not in module_source
