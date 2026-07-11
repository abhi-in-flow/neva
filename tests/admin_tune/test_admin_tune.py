"""Focused coverage for tune-admin contracts, routes, service, and filesystem.

Tests use temporary directories and dependency fakes only. They do not import
the isolated tuning harness, launch GPU work, access Postgres, or modify real
runtime data.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin_tune import router
from app.config import Settings
from app.deck_admin.deps import require_deck_admin_key
from app.tune_admin.config import TuneAdminConfig
from app.tune_admin.deps import get_tune_admin_service
from app.tune_admin.repository import (
    TuneAdminBusyError,
    TuneAdminRepository,
)
from app.tune_admin.service import TuneAdminService, TuneAdminServiceError
from contracts.api_types import AdminTuneArtifactProfile

NOW = datetime(2026, 7, 11, 13, 0, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64


def _config(tmp_path: Path, *, max_upload_bytes: int = 32) -> TuneAdminConfig:
    """Build isolated tune-admin configuration rooted in pytest temporary data."""
    return TuneAdminConfig(
        data_dir=tmp_path,
        runtime_dir_name="tune-demo",
        overview_filename="overview.json",
        requests_dir_name="requests",
        jobs_dir_name="jobs",
        uploads_dir_name="uploads",
        published_dir_name="published",
        published_audio_dir_name="audio",
        queue_lock_filename=".api-queue.lock",
        max_upload_bytes=max_upload_bytes,
        upload_chunk_bytes=4,
        allowed_audio_types=("audio/flac", "audio/webm"),
        audio_type_extensions=(("audio/flac", ".flac"), ("audio/webm", ".webm")),
        default_upload_extension=".audio",
        heartbeat_stale_seconds=30.0,
        queue_lock_stale_seconds=10.0,
        max_events=10,
        max_samples=5,
        max_language_chars=80,
    )


def _overview(
    *,
    profile: str = "full",
    compatible: bool = True,
    full_hash: str = HASH_A,
    heartbeat_at: datetime = NOW,
) -> dict[str, object]:
    """Build one supervisor publication with approved held-out output."""
    return {
        "supervisor": {
            "status": "ready",
            "heartbeat_at": heartbeat_at.isoformat(),
        },
        "corpus": {
            "ready": True,
            "status": "frozen",
            "input_mode": "audio",
            "model_id": "unsloth/gemma-4-E4B-it",
            "sample_counts": {"total": 10, "train": 8, "holdout": 2},
            "language_counts": {"as-IN": 10},
            "source_corpus_sha256": HASH_A,
            "dataset_manifest_sha256": HASH_B,
        },
        "smoke_artifact": {
            "available": True,
            "compatible": True,
            "status": "completed",
            "profile": "smoke",
            "model_id": "unsloth/gemma-4-E4B-it",
            "source_corpus_sha256": HASH_A,
            "adapter_sha256": HASH_B,
            "training": {"max_steps": 1},
        },
        "full_artifact": {
            "available": True,
            "compatible": compatible,
            "status": "completed",
            "profile": profile,
            "model_id": "unsloth/gemma-4-E4B-it",
            "source_corpus_sha256": full_hash,
            "adapter_sha256": HASH_B,
            "lora": {"rank": 16},
            "training": {"duration_seconds": 30.0, "peak_vram_gib": 12.0},
        },
        "heldout_samples": [
            {
                "sample_id": "sample-1",
                "native_language": "Assamese",
                "target": "water pot",
                "audio_name": "sample-1.flac",
                "approved": True,
            }
        ],
        "heldout_comparisons": [
            {
                "sample_id": "sample-1",
                "target": "water pot",
                "base": "pot",
                "tuned": "water pot",
                "approved": True,
            }
        ],
    }


class _MemoryStore:
    """In-memory service store recording fixed queue protocol writes."""

    def __init__(self, overview: dict[str, object] | None) -> None:
        """Store one publication and initialize operation captures."""
        self.overview_payload = overview
        self.jobs: dict[UUID, dict[str, object]] = {}
        self.requests: dict[UUID, dict[str, object]] = {}
        self.uploads: dict[str, bytes] = {}
        self.busy = False

    def read_overview(self) -> dict[str, object] | None:
        """Return the configured publication."""
        return self.overview_payload

    def read_job(self, job_id: UUID) -> dict[str, object] | None:
        """Return a captured job by UUID."""
        return self.jobs.get(job_id)

    def read_active_job(self) -> dict[str, object] | None:
        """Return one captured queued/running job."""
        return next(
            (
                job
                for job in self.jobs.values()
                if job.get("status") in {"queued", "running"}
            ),
            None,
        )

    def enqueue(
        self,
        job_id: UUID,
        request_payload: dict[str, object],
        job_payload: dict[str, object],
    ) -> None:
        """Capture a queue operation or simulate a busy GPU."""
        if self.busy:
            raise TuneAdminBusyError("busy")
        self.requests[job_id] = request_payload
        self.jobs[job_id] = job_payload

    def stage_upload(self, upload_name: str, payload: bytes) -> Path:
        """Capture temporary audio under its server-generated name."""
        self.uploads[upload_name] = payload
        return Path(upload_name)

    def remove_upload(self, upload_name: str) -> None:
        """Remove a captured upload during rollback."""
        self.uploads.pop(upload_name, None)

    def resolve_published_audio(self, sample_id: str) -> Path | None:
        """Return no file; route audio tests use the real repository."""
        return None


def _service(
    store: _MemoryStore,
    config: TuneAdminConfig,
    *,
    job_id: UUID | None = None,
) -> TuneAdminService:
    """Build a deterministic service around an in-memory store."""
    return TuneAdminService(
        store,
        config,
        now=lambda: NOW,
        new_uuid=(lambda: job_id) if job_id is not None else uuid4,
    )


def _app(service: TuneAdminService) -> FastAPI:
    """Build an isolated FastAPI app with an injected tune service."""
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_tune_admin_service] = lambda: service
    return application


def test_router_registers_all_paths_with_shared_admin_auth() -> None:
    """Expose every accepted endpoint and retain shared admin-key dependency."""
    routes = {
        (route.path, method): route
        for route in router.routes
        for method in (route.methods or set())
    }
    assert ("/api/admin/tune/overview", "GET") in routes
    assert ("/api/admin/tune/jobs/train-smoke", "POST") in routes
    assert ("/api/admin/tune/jobs/infer-live", "POST") in routes
    assert ("/api/admin/tune/jobs/{job_id}", "GET") in routes
    assert ("/api/admin/tune/samples/{sample_id}/audio", "GET") in routes
    assert router.dependencies[0].dependency is require_deck_admin_key


def test_routes_require_valid_shared_admin_key(tmp_path: Path) -> None:
    """Reject absent/wrong keys and allow the existing deck admin key."""
    service = _service(_MemoryStore(_overview()), _config(tmp_path))
    application = _app(service)
    with (
        patch(
            "app.deck_admin.deps.get_settings",
            return_value=Settings(deck_admin_api_key="correct"),
        ),
        TestClient(application) as client,
    ):
        assert client.get("/api/admin/tune/overview").status_code == 401
        assert (
            client.get(
                "/api/admin/tune/overview",
                headers={"X-Deck-Admin-Key": "wrong"},
            ).status_code
            == 401
        )
        response = client.get(
            "/api/admin/tune/overview",
            headers={"X-Deck-Admin-Key": "correct"},
        )
    assert response.status_code == 200
    assert response.json()["full_adapter_ready"] is True


@pytest.mark.parametrize(
    ("profile", "compatible", "corpus_hash", "expected"),
    [
        ("full", True, HASH_A, True),
        ("smoke", True, HASH_A, False),
        ("full", False, HASH_A, False),
        ("full", True, HASH_B, False),
    ],
)
def test_full_adapter_gate_rejects_smoke_or_incompatible_artifacts(
    tmp_path: Path,
    profile: str,
    compatible: bool,
    corpus_hash: str,
    expected: bool,
) -> None:
    """Enable inference only for a compatible profile=full publication."""
    service = _service(
        _MemoryStore(
            _overview(
                profile=profile,
                compatible=compatible,
                full_hash=corpus_hash,
            )
        ),
        _config(tmp_path),
    )
    overview = service.overview()
    assert overview.full_adapter_ready is expected
    assert overview.smoke_artifact is not None
    assert overview.smoke_artifact.profile is AdminTuneArtifactProfile.SMOKE
    assert bool(overview.heldout_comparisons) is expected


def test_compatible_full_artifact_without_comparisons_stays_gated(
    tmp_path: Path,
) -> None:
    """Keep technical artifact metadata visible while approval is unavailable."""
    raw = _overview()
    raw["heldout_comparisons"] = []
    store = _MemoryStore(raw)
    service = _service(store, _config(tmp_path))
    overview = service.overview()
    assert overview.full_artifact is not None
    assert overview.full_artifact.compatible is True
    assert overview.full_artifact.profile is AdminTuneArtifactProfile.FULL
    assert overview.full_adapter_ready is False
    assert overview.heldout_comparisons == []
    assert overview.readiness_reason == "No approved qualitative comparison is available"

    with pytest.raises(TuneAdminServiceError) as caught:
        service.start_live_inference(
            audio=b"fLaC",
            content_type="audio/flac",
            native_language="Assamese",
        )
    assert caught.value.status_code == 503
    assert caught.value.detail == "No approved qualitative comparison is available"
    assert store.uploads == {}


def test_mismatched_approved_comparison_does_not_enable_inference(
    tmp_path: Path,
) -> None:
    """Ignore approved comparisons whose sample ID has no approved sample."""
    raw = _overview()
    comparison = raw["heldout_comparisons"][0]  # type: ignore[index]
    comparison["sample_id"] = "different-sample"  # type: ignore[index]
    overview = _service(_MemoryStore(raw), _config(tmp_path)).overview()
    assert overview.full_artifact is not None
    assert overview.full_artifact.compatible is True
    assert overview.full_adapter_ready is False
    assert overview.heldout_comparisons == []
    assert overview.readiness_reason == "No approved qualitative comparison is available"


def test_matching_approved_comparison_enables_live_inference(
    tmp_path: Path,
) -> None:
    """Enable and queue inference only after one approved sample/comparison pair."""
    job_id = uuid4()
    store = _MemoryStore(_overview())
    service = _service(store, _config(tmp_path), job_id=job_id)
    overview = service.overview()
    assert overview.full_adapter_ready is True
    assert [item.sample_id for item in overview.heldout_comparisons] == ["sample-1"]

    operation = service.start_live_inference(
        audio=b"fLaC",
        content_type="audio/flac",
        native_language="Assamese",
    )
    assert operation.job_id == job_id
    assert store.requests[job_id]["kind"] == "infer_live"
    assert store.requests[job_id]["upload_name"] == f"{job_id}.flac"


def test_supervisor_publication_timestamp_is_used_as_heartbeat(
    tmp_path: Path,
) -> None:
    """Treat each atomic overview refresh as supervisor liveness evidence."""
    raw = _overview()
    raw.pop("supervisor")
    raw["updated_at"] = NOW.isoformat()
    service = _service(_MemoryStore(raw), _config(tmp_path))
    assert service.overview().supervisor.healthy is True


def test_train_queue_uses_only_fixed_uuid_protocol_fields(tmp_path: Path) -> None:
    """Queue smoke proof without accepting model, command, or path input."""
    job_id = uuid4()
    store = _MemoryStore(_overview())
    operation = _service(store, _config(tmp_path), job_id=job_id).start_smoke_training()
    assert operation.job_id == job_id
    assert store.requests[job_id] == {
        "job_id": str(job_id),
        "kind": "train_smoke",
        "created_at": NOW.isoformat(),
    }
    assert store.jobs[job_id]["status"] == "queued"
    assert store.jobs[job_id]["stage"] == "queued"


def test_repository_atomically_publishes_both_queue_files(tmp_path: Path) -> None:
    """Write complete JSON files with no temporary siblings left behind."""
    config = _config(tmp_path)
    repository = TuneAdminRepository(config)
    job_id = uuid4()
    request = {
        "job_id": str(job_id),
        "kind": "train_smoke",
        "created_at": NOW.isoformat(),
    }
    job = {
        **request,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
    }
    repository.enqueue(job_id, request, job)
    assert json.loads((config.requests_dir / f"{job_id}.json").read_text()) == request
    assert json.loads((config.jobs_dir / f"{job_id}.json").read_text()) == job
    assert list(config.runtime_root.rglob("*.tmp")) == []


def test_repository_busy_state_prevents_second_request(tmp_path: Path) -> None:
    """Fail closed with 409 semantics while queued/running work exists."""
    config = _config(tmp_path)
    repository = TuneAdminRepository(config)
    first = uuid4()
    base = {"created_at": NOW.isoformat()}
    repository.enqueue(
        first,
        {"job_id": str(first), "kind": "train_smoke", **base},
        {
            "job_id": str(first),
            "kind": "train_smoke",
            "status": "running",
            "stage": "training",
            "progress": 0.5,
            **base,
        },
    )
    second = uuid4()
    with pytest.raises(TuneAdminBusyError):
        repository.enqueue(
            second,
            {"job_id": str(second), "kind": "train_smoke", **base},
            {
                "job_id": str(second),
                "kind": "train_smoke",
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                **base,
            },
        )
    assert not (config.requests_dir / f"{second}.json").exists()


def test_live_inference_validates_type_limit_and_rolls_back_when_busy(
    tmp_path: Path,
) -> None:
    """Reject unsupported/oversized audio and remove staged bytes on 409."""
    config = _config(tmp_path, max_upload_bytes=4)
    store = _MemoryStore(_overview())
    service = _service(store, config)
    with pytest.raises(TuneAdminServiceError) as unsupported:
        service.start_live_inference(
            audio=b"fLaC",
            content_type="application/octet-stream",
            native_language="Assamese",
        )
    assert unsupported.value.status_code == 415
    with pytest.raises(TuneAdminServiceError) as oversized:
        service.start_live_inference(
            audio=b"12345",
            content_type="audio/flac",
            native_language="Assamese",
        )
    assert oversized.value.status_code == 413
    store.busy = True
    with pytest.raises(TuneAdminServiceError) as busy:
        service.start_live_inference(
            audio=b"fLaC",
            content_type="audio/flac",
            native_language="Assamese",
        )
    assert busy.value.status_code == 409
    assert store.uploads == {}


def test_multipart_route_enforces_streaming_upload_limit(tmp_path: Path) -> None:
    """Return 413 before the service receives an oversized multipart upload."""
    config = _config(tmp_path, max_upload_bytes=4)
    service = _service(_MemoryStore(_overview()), config)
    application = _app(service)
    application.dependency_overrides[require_deck_admin_key] = lambda: None
    with (
        patch("app.api.admin_tune.get_tune_admin_config", return_value=config),
        TestClient(application) as client,
    ):
        response = client.post(
            "/api/admin/tune/jobs/infer-live",
            data={"native_language": "Assamese"},
            files={"audio": ("ignored.webm", b"12345", "audio/webm")},
        )
    assert response.status_code == 413
    assert "12345" not in response.text


def test_job_status_redacts_paths_commands_and_raw_logs(tmp_path: Path) -> None:
    """Never expose sensitive runtime text from malformed supervisor output."""
    job_id = uuid4()
    store = _MemoryStore(_overview())
    store.jobs[job_id] = {
        "job_id": str(job_id),
        "kind": "infer_live",
        "status": "failed",
        "stage": "compare",
        "progress": 1,
        "created_at": NOW.isoformat(),
        "completed_at": NOW.isoformat(),
        "events": [
            {
                "timestamp": NOW.isoformat(),
                "stage": "compare",
                "message": "command failed at /home/operator/private",
            }
        ],
        "failure_reason": "Traceback: token secret at /tmp/raw.log",
        "result": {"base": "safe base", "tuned": "safe tuned"},
    }
    detail = _service(store, _config(tmp_path)).get_job(job_id)
    serialized = detail.model_dump_json()
    assert "/home/" not in serialized
    assert "/tmp/" not in serialized
    assert "Traceback" not in serialized
    assert "secret" not in serialized
    assert detail.events[0].message == "Progress update"
    assert detail.failure_reason == "Tune job failed"


def test_live_job_result_reads_supervisor_prediction_list(tmp_path: Path) -> None:
    """Expose only base/tuned text from the supervisor's safe prediction list."""
    job_id = uuid4()
    store = _MemoryStore(_overview())
    store.jobs[job_id] = {
        "job_id": str(job_id),
        "kind": "infer_live",
        "status": "completed",
        "stage": "completed",
        "progress": 1,
        "created_at": NOW.isoformat(),
        "completed_at": NOW.isoformat(),
        "events": [],
        "result": {
            "kind": "infer_live",
            "model_id": "unsloth/gemma-4-E4B-it",
            "predictions": [
                {
                    "utterance_id": "temporary-live-demo",
                    "target": "(unknown)",
                    "base": "base answer",
                    "tuned": "tuned answer",
                    "audio_path": "/home/private/live.flac",
                }
            ],
        },
    }
    detail = _service(store, _config(tmp_path)).get_job(job_id)
    assert detail.result is not None
    assert detail.result.base_output == "base answer"
    assert detail.result.tuned_output == "tuned answer"
    assert "/home/private" not in detail.model_dump_json()


def test_malformed_overview_and_job_return_safe_503(tmp_path: Path) -> None:
    """Map malformed runtime JSON to path-free safe service errors."""
    config = _config(tmp_path)
    repository = TuneAdminRepository(config)
    repository.ensure_runtime_directories()
    config.overview_path.write_text("{not-json", encoding="utf-8")
    service = TuneAdminService(repository, config, now=lambda: NOW)
    with pytest.raises(TuneAdminServiceError) as overview_error:
        service.overview()
    assert overview_error.value.status_code == 503
    assert str(tmp_path) not in overview_error.value.detail

    config.overview_path.unlink()
    job_id = uuid4()
    (config.jobs_dir / f"{job_id}.json").write_text("[]", encoding="utf-8")
    with pytest.raises(TuneAdminServiceError) as job_error:
        service.get_job(job_id)
    assert job_error.value.status_code == 503
    assert str(tmp_path) not in job_error.value.detail


def test_heldout_audio_rejects_traversal_and_confines_valid_file(
    tmp_path: Path,
) -> None:
    """Resolve only basenames under the published audio directory."""
    config = _config(tmp_path)
    repository = TuneAdminRepository(config)
    repository.ensure_runtime_directories()
    payload = _overview()
    sample = payload["heldout_samples"][0]  # type: ignore[index]
    sample["audio_name"] = "../../private.flac"  # type: ignore[index]
    config.overview_path.write_text(json.dumps(payload), encoding="utf-8")
    assert repository.resolve_published_audio("sample-1") is None

    sample["audio_name"] = "sample-1.flac"  # type: ignore[index]
    config.overview_path.write_text(json.dumps(payload), encoding="utf-8")
    audio = config.published_audio_dir / "sample-1.flac"
    audio.write_bytes(b"fLaC")
    assert repository.resolve_published_audio("sample-1") == audio.resolve()
    assert repository.resolve_published_audio("../sample-1") is None


def test_unknown_job_returns_404(tmp_path: Path) -> None:
    """Return the required 404 for an unknown UUID."""
    service = _service(_MemoryStore(_overview()), _config(tmp_path))
    with pytest.raises(TuneAdminServiceError) as caught:
        service.get_job(uuid4())
    assert caught.value.status_code == 404
