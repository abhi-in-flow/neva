"""Dependency-light tests for the single-GPU tuning demo supervisor.

All queue files, uploads, manifests, and child outputs live under pytest's
temporary directory. An injected subprocess runner proves fixed argv and
status behavior without importing model libraries or using a GPU.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.tune_demo_supervisor import (
    SupervisorSettings,
    TuneDemoSupervisor,
    dry_run,
    run_streaming_subprocess,
    validate_request,
)
from tune.manifest import sha256_directory, sha256_file

LOGGER = logging.getLogger(__name__)


def make_settings(
    root: Path,
    approved_sample_ids: frozenset[str] = frozenset(),
) -> SupervisorSettings:
    """Build isolated settings with short deterministic test limits."""
    LOGGER.info("make_settings called root_name=%s", root.name)
    return SupervisorSettings(
        runtime_root=root,
        prepared_dir=root / "prepared",
        full_adapter=root / "full" / "adapter",
        artifact_manifest=root / "full" / "artifact_manifest.json",
        run_root=root / "runs",
        approved_predictions=root / "approved-predictions.jsonl",
        approved_sample_ids=approved_sample_ids,
        preflight_timeout_seconds=10,
        train_timeout_seconds=20,
        infer_timeout_seconds=15,
        processing_stale_seconds=30,
        upload_ttl_seconds=60,
        heartbeat_interval_seconds=1,
    )


class FakeRunner:
    """Simulate preflight, training, and inference child processes."""

    def __init__(self) -> None:
        """Initialize an empty fixed-command capture."""
        LOGGER.info("FakeRunner.__init__ called")
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Capture argv and create the declared safe child outputs."""
        LOGGER.info(
            "FakeRunner.__call__ called executable=%s arg_count=%d",
            command[0],
            len(command) - 1,
        )
        self.calls.append((command, kwargs))
        if command[-1] == "--json":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps([{"name": "gpu", "passed": True, "detail": "fixture"}]),
                stderr="",
            )
        event_path = Path(command[command.index("--events") + 1])
        result_path = Path(command[command.index("--result") + 1])
        event_path.parent.mkdir(parents=True, exist_ok=True)
        event_path.write_text(
            json.dumps(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "stage": "completed",
                    "progress": 1.0,
                    "message": "fixture complete",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        kind = "infer_live" if "tune.compare" in command else "train"
        payload: dict[str, Any] = {
            "status": "completed",
            "kind": kind,
            "model_id": "fixture-model",
            "sample_count": 1,
            "unsafe_path": "/secret/audio.flac",
        }
        if kind == "infer_live":
            payload["predictions"] = [
                {
                    "utterance_id": "temporary-live-demo",
                    "target": "(live target not known)",
                    "base": "base output",
                    "tuned": "tuned output /secret/generated.txt",
                    "audio_path": "/secret/audio.flac",
                }
            ]
        else:
            payload["training_proof"] = {
                "available": True,
                "compatible": True,
                "status": "completed",
                "profile": "smoke",
                "model_id": "fixture-model",
                "input_mode": "audio",
                "sample_counts": {"total": 1, "train": 1, "holdout": 0},
                "language_counts": {"as-IN": 1},
                "lora_rank": 16,
                "completed_steps": 1,
                "duration_seconds": 1.0,
                "peak_vram_gib": 2.0,
                "source_corpus_sha256": "a" * 64,
                "adapter_sha256": "b" * 64,
                "created_at": datetime.now(UTC).isoformat(),
            }
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def write_request(path: Path, payload: dict[str, Any]) -> None:
    """Write one isolated browser request fixture."""
    LOGGER.info("write_request called path_name=%s kind=%s", path.name, payload.get("kind"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_publication_fixture(settings: SupervisorSettings, audio: Path) -> None:
    """Create matching prepared, artifact, metrics, and adapter fixture files."""
    LOGGER.info(
        "write_publication_fixture called runtime_name=%s audio_name=%s",
        settings.runtime_root.name,
        audio.name,
    )
    settings.prepared_dir.mkdir(parents=True, exist_ok=True)
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"fLaC-approved")
    train_row = {
        "utterance_id": "train-1",
        "native_lang_tag": "as-IN",
        "target": "fish trap",
        "input_mode": "audio",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "audio", "audio": str(audio)},
                    {"type": "text", "text": "Translate."},
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "fish trap"}]},
        ],
    }
    holdout_row = {**train_row, "utterance_id": "approved-1", "target": "water pot"}
    train_path = settings.prepared_dir / "train.jsonl"
    holdout_path = settings.prepared_dir / "holdout.jsonl"
    train_path.write_text(json.dumps(train_row) + "\n", encoding="utf-8")
    holdout_path.write_text(json.dumps(holdout_row) + "\n", encoding="utf-8")
    dataset = {
        "schema_version": 1,
        "kind": "dataset",
        "status": "frozen",
        "created_at": datetime.now(UTC).isoformat(),
        "model_id": "unsloth/gemma-4-E4B-it",
        "input_mode": "audio",
        "source_corpus_sha256": "a" * 64,
        "train_sha256": sha256_file(train_path),
        "holdout_sha256": sha256_file(holdout_path),
        "split_seed": 20260711,
        "holdout_fraction": 0.5,
        "sample_counts": {"total": 2, "train": 1, "holdout": 1},
        "language_counts": {"as-IN": 2},
    }
    dataset_path = settings.prepared_dir / "dataset_manifest.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
    settings.full_adapter.mkdir(parents=True, exist_ok=True)
    (settings.full_adapter / "adapter_model.safetensors").write_bytes(b"adapter")
    artifact = {
        "schema_version": 1,
        "kind": "adapter",
        "status": "completed",
        "created_at": datetime.now(UTC).isoformat(),
        "model_id": dataset["model_id"],
        "dataset_manifest_sha256": sha256_file(dataset_path),
        "source_corpus_sha256": dataset["source_corpus_sha256"],
        "train_sha256": dataset["train_sha256"],
        "holdout_sha256": dataset["holdout_sha256"],
        "split_seed": dataset["split_seed"],
        "sample_counts": dataset["sample_counts"],
        "language_counts": dataset["language_counts"],
        "lora": {"rank": 16},
        "training": {
            "profile": "full",
            "duration_seconds": 12.5,
            "peak_vram_gib": 10.0,
            "max_steps": 4,
        },
        "adapter_sha256": sha256_directory(settings.full_adapter),
    }
    settings.artifact_manifest.parent.mkdir(parents=True, exist_ok=True)
    settings.artifact_manifest.write_text(json.dumps(artifact), encoding="utf-8")
    (settings.full_adapter.parent / "training_metrics.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "profile": "full",
                "model_id": dataset["model_id"],
                "input_mode": "audio",
                "duration_seconds": 12.5,
                "peak_vram_gib": 10.0,
                "trainer_metrics": {"global_step": 4, "train_loss": 0.25},
            }
        ),
        encoding="utf-8",
    )


def test_train_smoke_uses_preflight_and_fixed_one_step_command(tmp_path: Path) -> None:
    """Run smoke training while proving full training and shell execution are disabled."""
    LOGGER.info("test_train_smoke_uses_preflight_and_fixed_one_step_command called")
    settings = make_settings(tmp_path / "runtime")
    runner = FakeRunner()
    supervisor = TuneDemoSupervisor(settings, runner=runner)
    supervisor.initialize()
    request = {
        "job_id": "smoke-1",
        "kind": "train_smoke",
        "created_at": datetime.now(UTC).isoformat(),
    }
    write_request(settings.requests_dir / "smoke-1.json", request)

    assert supervisor.run_once() is True

    status = json.loads((settings.jobs_dir / "smoke-1.json").read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert status["result"]["training_proof"]["profile"] == "smoke"
    assert len(runner.calls) == 2
    assert runner.calls[0][0][-2:] == ["tune.preflight", "--json"]
    train_command, kwargs = runner.calls[1]
    assert "tune.train" in train_command
    assert train_command[train_command.index("--max-steps") + 1] == "1"
    assert kwargs["shell"] is False


def test_infer_live_result_is_safe_and_not_implicitly_approved(tmp_path: Path) -> None:
    """Shape live outputs for jobs without publishing them as held-out evidence."""
    LOGGER.info("test_infer_live_consumes_upload_and_publishes_safe_prediction called")
    settings = make_settings(tmp_path / "runtime")
    runner = FakeRunner()
    supervisor = TuneDemoSupervisor(settings, runner=runner)
    supervisor.initialize()
    upload = settings.uploads_dir / "clip.webm"
    upload.write_bytes(b"temporary-audio")
    request = {
        "job_id": "infer-1",
        "kind": "infer_live",
        "created_at": datetime.now(UTC).isoformat(),
        "upload_name": upload.name,
        "native_language": "Assamese",
    }
    write_request(settings.requests_dir / "infer-1.json", request)

    assert supervisor.run_once() is True

    assert not upload.exists()
    status_text = (settings.jobs_dir / "infer-1.json").read_text(encoding="utf-8")
    overview_text = (settings.published_dir / "overview.json").read_text(encoding="utf-8")
    assert "/secret/" not in status_text
    assert "/secret/" not in overview_text
    overview = json.loads(overview_text)
    status = json.loads(status_text)
    assert status["result"]["base_output"] == "base output"
    assert status["result"]["tuned_output"] == "tuned output [redacted-path]"
    assert overview["heldout_samples"] == []
    assert overview["heldout_comparisons"] == []
    compare_command = runner.calls[1][0]
    assert "tune.compare" in compare_command
    assert compare_command[compare_command.index("--samples") + 1] == "1"


def test_overview_matches_backend_protocol_and_validates_full_adapter(tmp_path: Path) -> None:
    """Publish exact keys and mark only the hash-matching full artifact compatible."""
    LOGGER.info("test_overview_matches_backend_protocol_and_validates_full_adapter called")
    settings = make_settings(tmp_path / "runtime")
    supervisor = TuneDemoSupervisor(settings, runner=FakeRunner())
    supervisor.initialize()
    write_publication_fixture(settings, tmp_path / "source" / "approved.flac")

    overview = supervisor.publish_overview()

    assert set(overview) == {
        "supervisor",
        "corpus",
        "smoke_artifact",
        "full_artifact",
        "heldout_samples",
        "heldout_comparisons",
        "current_job",
    }
    assert set(overview["supervisor"]) == {"status", "heartbeat_at", "message"}
    assert set(overview["corpus"]) == {
        "ready",
        "status",
        "input_mode",
        "model_id",
        "sample_counts",
        "language_counts",
        "source_corpus_sha256",
        "dataset_manifest_sha256",
    }
    full = overview["full_artifact"]
    assert set(full) == {
        "available",
        "compatible",
        "status",
        "profile",
        "model_id",
        "input_mode",
        "sample_counts",
        "language_counts",
        "lora_rank",
        "completed_steps",
        "final_loss",
        "duration_seconds",
        "peak_vram_gib",
        "source_corpus_sha256",
        "adapter_sha256",
        "created_at",
    }
    assert full["available"] is True
    assert full["compatible"] is True
    assert full["profile"] == "full"
    assert full["completed_steps"] == 4
    assert full["final_loss"] == 0.25
    assert full["adapter_sha256"] == sha256_directory(settings.full_adapter)
    serialized = json.dumps(overview)
    assert str(tmp_path) not in serialized

    (settings.full_adapter / "adapter_model.safetensors").write_bytes(b"tampered")
    assert supervisor.publish_overview()["full_artifact"]["compatible"] is False


def test_operator_approval_cross_references_and_confines_audio_copy(tmp_path: Path) -> None:
    """Publish only allowlisted holdout predictions and copy audio under safe names."""
    LOGGER.info("test_operator_approval_cross_references_and_confines_audio_copy called")
    settings = make_settings(tmp_path / "runtime", frozenset({"approved-1", "missing-2"}))
    supervisor = TuneDemoSupervisor(settings, runner=FakeRunner())
    supervisor.initialize()
    source_audio = tmp_path / "outside-runtime" / "source.flac"
    write_publication_fixture(settings, source_audio)
    settings.approved_predictions.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "utterance_id": "approved-1",
                        "target": "water pot",
                        "base": "pot",
                        "tuned": "water pot",
                        "audio_path": "/ignored/untrusted.flac",
                    }
                ),
                json.dumps(
                    {
                        "utterance_id": "not-approved",
                        "target": "private",
                        "base": "private",
                        "tuned": "private",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rogue = settings.published_audio_dir / "rogue.flac"
    rogue.write_bytes(b"remove-me")

    overview = supervisor.publish_overview()

    assert overview["heldout_samples"] == [
        {
            "approved": True,
            "sample_id": "approved-1",
            "native_language": "as-IN",
            "target": "water pot",
            "audio_name": "approved-1.flac",
        }
    ]
    assert overview["heldout_comparisons"][0]["approved"] is True
    assert overview["heldout_comparisons"][0]["tuned_output"] == "water pot"
    copied = settings.published_audio_dir / "approved-1.flac"
    assert copied.read_bytes() == source_audio.read_bytes()
    assert copied.parent.resolve() == settings.published_audio_dir.resolve()
    assert not rogue.exists()


def test_overview_heartbeat_and_current_job_are_fresh(tmp_path: Path) -> None:
    """Refresh heartbeat and project one active job into the overview."""
    LOGGER.info("test_overview_heartbeat_and_current_job_are_fresh called")
    settings = make_settings(tmp_path / "runtime")
    supervisor = TuneDemoSupervisor(settings, runner=FakeRunner())
    supervisor.initialize()
    created_at = datetime.now(UTC).isoformat()
    job_id = "00000000-0000-4000-8000-000000000001"
    atomic_job = {
        "job_id": job_id,
        "kind": "train_smoke",
        "status": "running",
        "stage": "training",
        "progress": 0.5,
        "created_at": created_at,
        "started_at": created_at,
        "completed_at": None,
    }
    (settings.jobs_dir / f"{job_id}.json").write_text(json.dumps(atomic_job), encoding="utf-8")

    first = supervisor.publish_overview()
    second = supervisor.publish_overview()

    assert first["supervisor"]["status"] == "running"
    assert first["current_job"]["job_id"] == job_id
    assert datetime.fromisoformat(second["supervisor"]["heartbeat_at"]) >= datetime.fromisoformat(
        first["supervisor"]["heartbeat_at"]
    )


def test_recovery_cleanup_and_dry_run_are_isolated(tmp_path: Path, capsys: object) -> None:
    """Recover stale claims, expire uploads, and keep rehearsal mutation-free."""
    LOGGER.info("test_recovery_cleanup_and_dry_run_are_isolated called")
    settings = make_settings(tmp_path / "runtime")
    assert dry_run(settings) == 0
    assert not settings.runtime_root.exists()
    assert "full_training=disabled" in capsys.readouterr().out

    supervisor = TuneDemoSupervisor(settings, runner=FakeRunner())
    supervisor.initialize()
    stale_request = settings.requests_dir / "stale.processing.json"
    stale_request.write_text("{}", encoding="utf-8")
    stale_upload = settings.uploads_dir / "old.flac"
    stale_upload.write_bytes(b"old")
    old = datetime.now(UTC).timestamp() - 120
    os.utime(stale_request, (old, old))
    os.utime(stale_upload, (old, old))

    assert supervisor.recover_stale_processing() == 1
    assert (settings.requests_dir / "stale.json").is_file()
    assert supervisor.delete_expired_uploads() == 1
    assert not stale_upload.exists()


def test_request_cannot_supply_model_paths_or_commands(tmp_path: Path) -> None:
    """Reject browser fields that could alter the fixed execution boundary."""
    LOGGER.info("test_request_cannot_supply_model_paths_or_commands called")
    source = tmp_path / "unsafe.json"
    payload = {
        "job_id": "unsafe",
        "kind": "train_smoke",
        "created_at": datetime.now(UTC).isoformat(),
        "command": "python arbitrary.py",
        "model": "other/model",
        "output_path": "/tmp/escape",
    }

    with pytest.raises(ValueError, match="unsupported fields"):
        validate_request(payload, source)


def test_streaming_executor_forwards_child_events_without_shell(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Forward progress during a fake child process without loading GPU libraries."""
    LOGGER.info("test_streaming_executor_forwards_child_events_without_shell called")
    event_path = tmp_path / "events.jsonl"
    event_path.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "stage": "training",
                "progress": 0.5,
                "message": "halfway",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    callbacks: list[list[dict[str, Any]]] = []
    constructor_kwargs: dict[str, Any] = {}

    class FakeProcess:
        """Expose the minimal Popen polling protocol."""

        returncode = 0

        def __init__(self) -> None:
            """Initialize one running poll before successful completion."""
            LOGGER.info("FakeProcess.__init__ called")
            self.poll_count = 0

        def poll(self) -> int | None:
            """Remain running across heartbeat intervals, then complete."""
            LOGGER.info("FakeProcess.poll called poll_count=%d", self.poll_count)
            self.poll_count += 1
            return None if self.poll_count <= 3 else 0

        def kill(self) -> None:
            """Fail if the non-timeout fixture is unexpectedly killed."""
            raise AssertionError("fixture process should not be killed")

        def wait(self) -> int:
            """Return the successful fixture exit code."""
            return self.returncode

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        """Capture safe process construction and return the fake process."""
        LOGGER.info("fake_popen called executable=%s", command[0])
        constructor_kwargs.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr("scripts.tune_demo_supervisor.subprocess.Popen", fake_popen)
    monkeypatch.setattr("scripts.tune_demo_supervisor.time.sleep", lambda seconds: None)
    monotonic_values = iter((0.0, 2.0, 4.0, 6.0))
    monkeypatch.setattr(
        "scripts.tune_demo_supervisor.time.monotonic",
        lambda: next(monotonic_values),
    )

    completed = run_streaming_subprocess(
        ["fixed-command", "--fixed-arg"],
        timeout=10,
        event_path=event_path,
        on_events=callbacks.append,
        heartbeat_interval_seconds=1,
    )

    assert completed.returncode == 0
    assert constructor_kwargs["shell"] is False
    assert callbacks[-1][0]["stage"] == "training"
    assert len(callbacks) == 3
