"""Supervise browser-requested Gemma demo jobs on one host GPU.

This host-run process bridges a filesystem request queue to the isolated
``tune`` CLIs. It accepts only ``train_smoke`` and ``infer_live`` request
kinds, atomically claims one request at a time, executes fixed argv lists
without a shell, and publishes bounded status and overview JSON. Request data
can never select model identifiers, filesystem paths, commands, or full
training. ``--dry-run`` performs no filesystem mutation or subprocess work.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tune.manifest import (
    sha256_directory,
    sha256_file,
    validate_artifact_compatibility,
    validate_dataset_files,
)

LOGGER = logging.getLogger(__name__)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = REPOSITORY_ROOT / "data" / "tune-demo"
SUPPORTED_KINDS = frozenset({"train_smoke", "infer_live"})
STATUS_VALUES = frozenset({"queued", "running", "completed", "failed"})
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
UPLOAD_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9:])/(?:[^\s/]+/)*[^\s]+|[A-Za-z]:\\(?:[^\s\\]+\\)*[^\s]+"
)
ALLOWED_UPLOAD_SUFFIXES = frozenset({".flac", ".wav", ".webm", ".ogg", ".mp3", ".m4a"})
MAX_LANGUAGE_CHARS = 80
MAX_EVENTS = 100
MAX_PREDICTIONS = 10
MAX_PUBLIC_TEXT = 500
POLL_SECONDS = 1.0
CHILD_POLL_SECONDS = 0.25
SAMPLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class SupervisorSettings:
    """Hold centralized host paths, subprocess timeouts, and retention limits."""

    runtime_root: Path
    prepared_dir: Path
    full_adapter: Path
    artifact_manifest: Path
    run_root: Path
    approved_predictions: Path
    approved_sample_ids: frozenset[str]
    preflight_timeout_seconds: int
    train_timeout_seconds: int
    infer_timeout_seconds: int
    processing_stale_seconds: int
    upload_ttl_seconds: int
    heartbeat_interval_seconds: int

    @property
    def requests_dir(self) -> Path:
        """Return the browser-to-supervisor request queue directory."""
        LOGGER.info("SupervisorSettings.requests_dir called")
        return self.runtime_root / "requests"

    @property
    def jobs_dir(self) -> Path:
        """Return the public per-job status directory."""
        LOGGER.info("SupervisorSettings.jobs_dir called")
        return self.runtime_root / "jobs"

    @property
    def uploads_dir(self) -> Path:
        """Return the temporary browser upload directory."""
        LOGGER.info("SupervisorSettings.uploads_dir called")
        return self.runtime_root / "uploads"

    @property
    def published_dir(self) -> Path:
        """Return the safe public summary directory."""
        LOGGER.info("SupervisorSettings.published_dir called")
        return self.runtime_root / "published"

    @property
    def published_audio_dir(self) -> Path:
        """Return the confined operator-approved held-out audio directory."""
        LOGGER.info("SupervisorSettings.published_audio_dir called")
        return self.published_dir / "audio"


def env_path(name: str, default: Path) -> Path:
    """Load one absolute or repository-relative path from the environment."""
    LOGGER.info("env_path called name=%s override=%s", name, name in os.environ)
    value = os.getenv(name)
    path = Path(value).expanduser() if value else default
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def env_positive_int(name: str, default: int) -> int:
    """Load and validate one positive integer environment setting."""
    LOGGER.info("env_positive_int called name=%s override=%s", name, name in os.environ)
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def env_sample_ids(name: str) -> frozenset[str]:
    """Load a comma-separated allowlist of safe held-out sample identifiers."""
    LOGGER.info("env_sample_ids called name=%s override=%s", name, name in os.environ)
    values = frozenset(item.strip() for item in os.getenv(name, "").split(",") if item.strip())
    invalid = sorted(value for value in values if SAMPLE_ID_PATTERN.fullmatch(value) is None)
    if invalid:
        raise ValueError(f"{name} contains invalid sample identifiers")
    return values


def load_settings(runtime_root: Path | None = None) -> SupervisorSettings:
    """Load all supervisor settings without creating runtime directories."""
    LOGGER.info("load_settings called runtime_override=%s", runtime_root is not None)
    root = runtime_root or env_path("TUNE_DEMO_RUNTIME_ROOT", DEFAULT_RUNTIME_ROOT)
    if not root.is_absolute():
        root = REPOSITORY_ROOT / root
    heartbeat_interval = env_positive_int("TUNE_DEMO_HEARTBEAT_INTERVAL_SECONDS", 5)
    if heartbeat_interval >= 30:
        raise ValueError("TUNE_DEMO_HEARTBEAT_INTERVAL_SECONDS must be below 30")
    return SupervisorSettings(
        runtime_root=root.resolve(),
        prepared_dir=env_path("TUNE_DEMO_PREPARED_DIR", root / "prepared").resolve(),
        full_adapter=env_path("TUNE_DEMO_FULL_ADAPTER", root / "full" / "adapter").resolve(),
        artifact_manifest=env_path(
            "TUNE_DEMO_ARTIFACT_MANIFEST",
            root / "full" / "artifact_manifest.json",
        ).resolve(),
        run_root=env_path("TUNE_DEMO_RUN_ROOT", root / "runs").resolve(),
        approved_predictions=env_path(
            "TUNE_DEMO_APPROVED_PREDICTIONS",
            root / "approved-predictions.jsonl",
        ).resolve(),
        approved_sample_ids=env_sample_ids("TUNE_DEMO_APPROVED_SAMPLE_IDS"),
        preflight_timeout_seconds=env_positive_int("TUNE_DEMO_PREFLIGHT_TIMEOUT_SECONDS", 120),
        train_timeout_seconds=env_positive_int("TUNE_DEMO_TRAIN_TIMEOUT_SECONDS", 1800),
        infer_timeout_seconds=env_positive_int("TUNE_DEMO_INFER_TIMEOUT_SECONDS", 600),
        processing_stale_seconds=env_positive_int("TUNE_DEMO_PROCESSING_STALE_SECONDS", 2100),
        upload_ttl_seconds=env_positive_int("TUNE_DEMO_UPLOAD_TTL_SECONDS", 3600),
        heartbeat_interval_seconds=heartbeat_interval,
    )


def utc_now() -> str:
    """Return a timezone-aware UTC timestamp for request and status records."""
    LOGGER.info("utc_now called")
    return datetime.now(UTC).isoformat()


def bounded_text(value: object, limit: int = MAX_PUBLIC_TEXT) -> str:
    """Return one control-free, bounded line for browser-visible status."""
    LOGGER.info("bounded_text called value_type=%s limit=%d", type(value).__name__, limit)
    single_line = " ".join(str(value).split())
    return ABSOLUTE_PATH_PATTERN.sub("[redacted-path]", single_line)[:limit]


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a JSON object with fsync and atomic replacement."""
    LOGGER.info("atomic_write_json called path_name=%s keys=%s", path.name, sorted(payload))
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        json.dump(payload, temporary, ensure_ascii=False, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def read_json_object(path: Path) -> dict[str, Any]:
    """Read one JSON object and reject arrays or primitive values."""
    LOGGER.info("read_json_object called path_name=%s", path.name)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON document must be an object")
    return payload


def parse_created_at(value: object) -> str:
    """Validate a request timestamp and return its normalized source string."""
    LOGGER.info("parse_created_at called value_type=%s", type(value).__name__)
    if not isinstance(value, str):
        raise ValueError("created_at must be an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("created_at must include a timezone")
    return value


def validate_request(payload: dict[str, Any], source: Path) -> dict[str, str]:
    """Validate the fixed request protocol without accepting executable inputs."""
    LOGGER.info(
        "validate_request called source_name=%s keys=%s",
        source.name,
        sorted(payload),
    )
    job_id = payload.get("job_id")
    kind = payload.get("kind")
    if not isinstance(job_id, str) or JOB_ID_PATTERN.fullmatch(job_id) is None:
        raise ValueError("invalid job_id")
    expected_name = source.name.removesuffix(".processing.json").removesuffix(".json")
    if expected_name != job_id:
        raise ValueError("request filename must match job_id")
    if kind not in SUPPORTED_KINDS:
        raise ValueError("unsupported request kind")
    request: dict[str, str] = {
        "job_id": job_id,
        "kind": kind,
        "created_at": parse_created_at(payload.get("created_at")),
    }
    allowed = {"job_id", "kind", "created_at"}
    if kind == "infer_live":
        upload_name = payload.get("upload_name")
        language = payload.get("native_language")
        if (
            not isinstance(upload_name, str)
            or UPLOAD_NAME_PATTERN.fullmatch(upload_name) is None
            or Path(upload_name).suffix.lower() not in ALLOWED_UPLOAD_SUFFIXES
        ):
            raise ValueError("invalid upload_name")
        if not isinstance(language, str) or not bounded_text(language, MAX_LANGUAGE_CHARS):
            raise ValueError("native_language must be non-empty")
        request["upload_name"] = upload_name
        request["native_language"] = bounded_text(language, MAX_LANGUAGE_CHARS)
        allowed.update({"upload_name", "native_language"})
    if set(payload) != allowed:
        raise ValueError("request contains unsupported fields")
    return request


def safe_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return a bounded event containing only approved browser-visible fields."""
    LOGGER.info("safe_event called keys=%s", sorted(event))
    stage = event.get("stage")
    progress = event.get("progress")
    message = event.get("message")
    if not isinstance(stage, str) or not isinstance(progress, int | float):
        return None
    if not 0.0 <= float(progress) <= 1.0:
        return None
    safe: dict[str, Any] = {
        "timestamp": bounded_text(event.get("timestamp", utc_now()), 64),
        "stage": bounded_text(stage, 64),
        "progress": round(float(progress), 4),
        "message": bounded_text(message or stage, 240),
    }
    for key in ("profile", "sample_count"):
        value = event.get(key)
        if isinstance(value, str | int | float | bool):
            safe[key] = bounded_text(value, 80) if isinstance(value, str) else value
    return safe


def read_events(path: Path) -> list[dict[str, Any]]:
    """Read and sanitize a bounded tail of child-process JSONL events."""
    LOGGER.info("read_events called path_name=%s exists=%s", path.name, path.is_file())
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-MAX_EVENTS:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            event = safe_event(payload)
            if event is not None:
                events.append(event)
    return events


def safe_predictions(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Extract bounded prediction fields while dropping paths and raw inputs."""
    LOGGER.info("safe_predictions called keys=%s", sorted(payload))
    rows = payload.get("predictions")
    if not isinstance(rows, list):
        return []
    predictions: list[dict[str, str]] = []
    for row in rows[:MAX_PREDICTIONS]:
        if not isinstance(row, dict):
            continue
        predictions.append(
            {
                key: bounded_text(row.get(key, ""))
                for key in ("utterance_id", "target", "base", "tuned")
            }
        )
    return predictions


def safe_aggregate_mapping(value: object) -> dict[str, int]:
    """Return bounded nonnegative aggregate counts from a manifest mapping."""
    LOGGER.info("safe_aggregate_mapping called value_type=%s", type(value).__name__)
    if not isinstance(value, dict):
        return {}
    safe: dict[str, int] = {}
    for key, count in list(value.items())[:50]:
        if isinstance(key, str) and isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            safe[bounded_text(key, 80)] = count
    return safe


def safe_sample_counts(value: object) -> dict[str, int]:
    """Return total, train, and holdout counts with zero-safe defaults."""
    LOGGER.info("safe_sample_counts called value_type=%s", type(value).__name__)
    raw = value if isinstance(value, dict) else {}
    return {
        key: (
            raw.get(key)
            if isinstance(raw.get(key), int)
            and not isinstance(raw.get(key), bool)
            and raw.get(key) >= 0
            else 0
        )
        for key in ("total", "train", "holdout")
    }


def safe_artifact_payload(payload: object) -> dict[str, Any] | None:
    """Whitelist backend artifact metadata without retaining local paths."""
    LOGGER.info("safe_artifact_payload called value_type=%s", type(payload).__name__)
    if not isinstance(payload, dict):
        return None
    artifact: dict[str, Any] = {
        "available": bool(payload.get("available")),
        "compatible": bool(payload.get("compatible")),
        "status": bounded_text(payload.get("status", "unavailable"), 40),
        "profile": bounded_text(payload.get("profile", ""), 20) or None,
        "model_id": bounded_text(payload.get("model_id", ""), 160) or None,
        "input_mode": bounded_text(payload.get("input_mode", ""), 20) or None,
        "sample_counts": safe_sample_counts(payload.get("sample_counts")),
        "language_counts": safe_aggregate_mapping(payload.get("language_counts")),
    }
    for key in ("lora_rank", "completed_steps"):
        value = payload.get(key)
        artifact[key] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    for key in ("final_loss", "duration_seconds", "peak_vram_gib"):
        value = payload.get(key)
        artifact[key] = value if isinstance(value, int | float) and not isinstance(value, bool) else None
    for key in ("source_corpus_sha256", "adapter_sha256", "created_at"):
        value = payload.get(key)
        artifact[key] = bounded_text(value, 128) if isinstance(value, str) else None
    return artifact


def safe_child_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape operation-specific results exactly for the backend job protocol."""
    LOGGER.info("safe_child_result called kind=%s", payload.get("kind"))
    if payload.get("kind") == "train":
        proof = safe_artifact_payload(payload.get("training_proof"))
        return {"training_proof": proof} if proof is not None else {}
    predictions = safe_predictions(payload)
    first = predictions[0] if predictions else {}
    base = payload.get("base_output", first.get("base"))
    tuned = payload.get("tuned_output", first.get("tuned"))
    return {
        "base_output": bounded_text(base, 2000) if isinstance(base, str) else None,
        "tuned_output": bounded_text(tuned, 2000) if isinstance(tuned, str) else None,
    }


def build_status(
    request: dict[str, str],
    status: str,
    stage: str,
    progress: float,
    *,
    events: list[dict[str, Any]] | None = None,
    result: dict[str, Any] | None = None,
    failure_reason: str | None = None,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one protocol-compliant status document with safe timestamps."""
    LOGGER.info(
        "build_status called job_id=%s status=%s stage=%s progress=%s",
        request["job_id"],
        status,
        stage,
        progress,
    )
    if status not in STATUS_VALUES or not 0.0 <= progress <= 1.0:
        raise ValueError("invalid status transition")
    now = utc_now()
    prior = previous or {}
    payload: dict[str, Any] = {
        "job_id": request["job_id"],
        "kind": request["kind"],
        "status": status,
        "stage": bounded_text(stage, 64),
        "progress": round(progress, 4),
        "events": (events or [])[-MAX_EVENTS:],
        "result": result,
        "failure_reason": bounded_text(failure_reason) if failure_reason else None,
        "created_at": prior.get("created_at", request["created_at"]),
        "queued_at": prior.get("queued_at", now),
        "started_at": prior.get("started_at") or (now if status == "running" else None),
        "completed_at": now if status in {"completed", "failed"} else None,
        "updated_at": now,
    }
    return payload


def run_subprocess(
    command: Sequence[str],
    *,
    timeout: int,
    runner: CommandRunner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    """Execute fixed argv without a shell and capture bounded diagnostic output."""
    LOGGER.info(
        "run_subprocess called executable=%s arg_count=%d timeout=%d",
        command[0],
        len(command) - 1,
        timeout,
    )
    return runner(
        list(command),
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )


def run_captured_heartbeat_subprocess(
    command: Sequence[str],
    *,
    timeout: int,
    heartbeat_interval_seconds: int,
    on_heartbeat: Callable[[], None],
) -> subprocess.CompletedProcess[str]:
    """Run fixed argv with captured stdout and periodic supervisor heartbeats."""
    LOGGER.info(
        "run_captured_heartbeat_subprocess called executable=%s timeout=%d",
        command[0],
        timeout,
    )
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output:
        process = subprocess.Popen(
            list(command),
            cwd=REPOSITORY_ROOT,
            shell=False,
            stdout=output,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        started = time.monotonic()
        last_heartbeat = started - heartbeat_interval_seconds
        while process.poll() is None:
            current = time.monotonic()
            if current - last_heartbeat >= heartbeat_interval_seconds:
                on_heartbeat()
                last_heartbeat = current
            if current - started >= timeout:
                process.kill()
                process.wait()
                raise subprocess.TimeoutExpired(list(command), timeout)
            time.sleep(CHILD_POLL_SECONDS)
        output.seek(0)
        stdout = output.read()
    return subprocess.CompletedProcess(list(command), process.returncode, stdout=stdout, stderr="")


def run_streaming_subprocess(
    command: Sequence[str],
    *,
    timeout: int,
    event_path: Path,
    on_events: Callable[[list[dict[str, Any]]], None],
    heartbeat_interval_seconds: int,
) -> subprocess.CompletedProcess[str]:
    """Run fixed argv while forwarding newly written safe progress events."""
    LOGGER.info(
        "run_streaming_subprocess called executable=%s arg_count=%d timeout=%d",
        command[0],
        len(command) - 1,
        timeout,
    )
    process = subprocess.Popen(
        list(command),
        cwd=REPOSITORY_ROOT,
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    started = time.monotonic()
    last_callback = started - heartbeat_interval_seconds
    observed_count = -1
    while process.poll() is None:
        events = read_events(event_path)
        current = time.monotonic()
        if (
            len(events) != observed_count
            or current - last_callback >= heartbeat_interval_seconds
        ):
            observed_count = len(events)
            on_events(events)
            last_callback = current
        if current - started >= timeout:
            process.kill()
            process.wait()
            raise subprocess.TimeoutExpired(list(command), timeout)
        time.sleep(CHILD_POLL_SECONDS)
    events = read_events(event_path)
    if len(events) != observed_count:
        on_events(events)
    return subprocess.CompletedProcess(list(command), process.returncode, stdout="", stderr="")


class TuneDemoSupervisor:
    """Claim and execute one filesystem-backed tuning demo job at a time."""

    def __init__(
        self,
        settings: SupervisorSettings,
        *,
        runner: CommandRunner = subprocess.run,
    ) -> None:
        """Store centralized settings and an injectable dependency-light runner."""
        LOGGER.info("TuneDemoSupervisor.__init__ called runtime_name=%s", settings.runtime_root.name)
        self.settings = settings
        self.runner = runner

    def initialize(self) -> None:
        """Create the owned runtime directory layout."""
        LOGGER.info("TuneDemoSupervisor.initialize called")
        for directory in (
            self.settings.requests_dir,
            self.settings.jobs_dir,
            self.settings.uploads_dir,
            self.settings.published_dir,
            self.settings.published_audio_dir,
            self.settings.run_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def recover_stale_processing(self, now: float | None = None) -> int:
        """Atomically requeue processing requests left stale by an interrupted host."""
        LOGGER.info("TuneDemoSupervisor.recover_stale_processing called")
        current = time.time() if now is None else now
        recovered = 0
        for processing in sorted(self.settings.requests_dir.glob("*.processing.json")):
            if current - processing.stat().st_mtime < self.settings.processing_stale_seconds:
                continue
            request_name = processing.name.replace(".processing.json", ".json")
            destination = processing.with_name(request_name)
            if destination.exists():
                processing.unlink()
                continue
            os.replace(processing, destination)
            recovered += 1
        return recovered

    def delete_expired_uploads(self, now: float | None = None) -> int:
        """Delete regular temporary uploads older than the configured TTL."""
        LOGGER.info("TuneDemoSupervisor.delete_expired_uploads called")
        current = time.time() if now is None else now
        deleted = 0
        for upload in self.settings.uploads_dir.iterdir():
            if (
                upload.is_file()
                and not upload.is_symlink()
                and current - upload.stat().st_mtime >= self.settings.upload_ttl_seconds
            ):
                upload.unlink()
                deleted += 1
        return deleted

    def claim_next(self) -> Path | None:
        """Atomically rename the oldest queued request into processing state."""
        LOGGER.info("TuneDemoSupervisor.claim_next called")
        candidates = sorted(
            self.settings.requests_dir.glob("*.json"),
            key=lambda item: (item.stat().st_mtime, item.name),
        )
        for request_path in candidates:
            processing = request_path.with_name(
                request_path.name.removesuffix(".json") + ".processing.json"
            )
            try:
                os.replace(request_path, processing)
            except FileNotFoundError:
                continue
            return processing
        return None

    def status_path(self, job_id: str) -> Path:
        """Return the fixed public status path for a validated job identifier."""
        LOGGER.info("TuneDemoSupervisor.status_path called job_id=%s", job_id)
        return self.settings.jobs_dir / f"{job_id}.json"

    def command_paths(self, job_id: str) -> tuple[Path, Path, Path]:
        """Return fixed run, event, and result locations for one validated job."""
        LOGGER.info("TuneDemoSupervisor.command_paths called job_id=%s", job_id)
        job_root = self.settings.run_root / job_id
        return job_root, job_root / "events.jsonl", job_root / "result.json"

    def preflight(self, on_heartbeat: Callable[[], None] | None = None) -> list[dict[str, Any]]:
        """Run and parse the existing machine-readable tuning preflight command."""
        LOGGER.info("TuneDemoSupervisor.preflight called")
        command = ["uv", "run", "--project", "tune", "python", "-m", "tune.preflight", "--json"]
        if self.runner is subprocess.run:
            completed = run_captured_heartbeat_subprocess(
                command,
                timeout=self.settings.preflight_timeout_seconds,
                heartbeat_interval_seconds=self.settings.heartbeat_interval_seconds,
                on_heartbeat=on_heartbeat or self.publish_overview,
            )
        else:
            if on_heartbeat is not None:
                on_heartbeat()
            completed = run_subprocess(
                command,
                timeout=self.settings.preflight_timeout_seconds,
                runner=self.runner,
            )
        if completed.returncode != 0:
            raise RuntimeError("tuning preflight failed")
        payload = json.loads(completed.stdout)
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) and item.get("passed") is True for item in payload
        ):
            raise RuntimeError("tuning preflight returned invalid or failing checks")
        return payload

    def build_command(
        self,
        request: dict[str, str],
        event_path: Path,
        result_path: Path,
        job_root: Path,
    ) -> tuple[list[str], int]:
        """Build one of two fixed command templates from validated scalar inputs."""
        LOGGER.info(
            "TuneDemoSupervisor.build_command called job_id=%s kind=%s",
            request["job_id"],
            request["kind"],
        )
        common = ["uv", "run", "--project", "tune", "python", "-m"]
        prepared = self.settings.prepared_dir
        if request["kind"] == "train_smoke":
            return (
                common
                + [
                    "tune.train",
                    "--train",
                    str(prepared / "train.jsonl"),
                    "--dataset-manifest",
                    str(prepared / "dataset_manifest.json"),
                    "--output",
                    str(job_root / "artifacts"),
                    "--max-steps",
                    "1",
                    "--events",
                    str(event_path),
                    "--result",
                    str(result_path),
                ],
                self.settings.train_timeout_seconds,
            )
        upload = self.settings.uploads_dir / request["upload_name"]
        if not upload.is_file() or upload.is_symlink():
            raise ValueError("temporary upload does not exist")
        return (
            common
            + [
                "tune.compare",
                "--holdout",
                str(prepared / "holdout.jsonl"),
                "--dataset-manifest",
                str(prepared / "dataset_manifest.json"),
                "--adapter",
                str(self.settings.full_adapter),
                "--artifact-manifest",
                str(self.settings.artifact_manifest),
                "--samples",
                "1",
                "--live-audio",
                str(upload),
                "--native-language",
                request["native_language"],
                "--events",
                str(event_path),
                "--result",
                str(result_path),
            ],
            self.settings.infer_timeout_seconds,
        )

    def execute(self, request: dict[str, str]) -> dict[str, Any]:
        """Run preflight and one bounded smoke-training or live-inference command."""
        LOGGER.info(
            "TuneDemoSupervisor.execute called job_id=%s kind=%s",
            request["job_id"],
            request["kind"],
        )
        job_root, event_path, result_path = self.command_paths(request["job_id"])
        job_root.mkdir(parents=True, exist_ok=False)
        status_path = self.status_path(request["job_id"])
        queued = build_status(request, "queued", "queued", 0.0)
        atomic_write_json(status_path, queued)
        running = build_status(
            request,
            "running",
            "preflight",
            0.02,
            previous=queued,
        )
        atomic_write_json(status_path, running)
        self.publish_overview()

        def heartbeat_preflight() -> None:
            """Refresh running status and overview during a long preflight."""
            nonlocal running
            LOGGER.info("heartbeat_preflight called job_id=%s", request["job_id"])
            running = build_status(
                request,
                "running",
                "preflight",
                running["progress"],
                events=running["events"],
                previous=running,
            )
            atomic_write_json(status_path, running)
            self.publish_overview()

        self.preflight(on_heartbeat=heartbeat_preflight)
        command, timeout = self.build_command(request, event_path, result_path, job_root)
        running = build_status(
            request,
            "running",
            "executing",
            0.1,
            events=read_events(event_path),
            previous=running,
        )
        atomic_write_json(status_path, running)

        def update_running(events: list[dict[str, Any]]) -> None:
            """Atomically publish the most recent child progress event."""
            nonlocal running
            LOGGER.info(
                "update_running called job_id=%s event_count=%d",
                request["job_id"],
                len(events),
            )
            latest = events[-1] if events else {"stage": "executing", "progress": 0.0}
            mapped_progress = min(0.95, 0.1 + 0.85 * float(latest["progress"]))
            running = build_status(
                request,
                "running",
                str(latest["stage"]),
                mapped_progress,
                events=events,
                previous=running,
            )
            atomic_write_json(status_path, running)
            self.publish_overview()

        if self.runner is subprocess.run:
            completed = run_streaming_subprocess(
                command,
                timeout=timeout,
                event_path=event_path,
                on_events=update_running,
                heartbeat_interval_seconds=self.settings.heartbeat_interval_seconds,
            )
        else:
            completed = run_subprocess(command, timeout=timeout, runner=self.runner)
            update_running(read_events(event_path))
        events = read_events(event_path)
        if completed.returncode != 0:
            LOGGER.error(
                "tuning child failed kind=%s returncode=%d stderr_length=%d",
                request["kind"],
                completed.returncode,
                len(completed.stderr),
            )
            reason = f"{request['kind']} command exited with code {completed.returncode}"
            failed = build_status(
                request,
                "failed",
                "failed",
                1.0,
                events=events,
                failure_reason=reason,
                previous=running,
            )
            atomic_write_json(status_path, failed)
            return failed
        if not result_path.is_file():
            raise RuntimeError("tuning command completed without a result document")
        result = safe_child_result(read_json_object(result_path))
        final = build_status(
            request,
            "completed",
            "completed",
            1.0,
            events=events,
            result=result,
            previous=running,
        )
        atomic_write_json(status_path, final)
        return final

    def process_claim(self, processing: Path) -> dict[str, Any]:
        """Validate and process one claimed request, always consuming its queue file."""
        LOGGER.info("TuneDemoSupervisor.process_claim called source_name=%s", processing.name)
        request: dict[str, str] | None = None
        try:
            request = validate_request(read_json_object(processing), processing)
            final = self.execute(request)
        except Exception as exc:
            LOGGER.exception("processing request failed error_type=%s", type(exc).__name__)
            if request is None:
                inferred_id = processing.name.removesuffix(".processing.json")
                if JOB_ID_PATTERN.fullmatch(inferred_id) is None:
                    inferred_id = f"invalid-{int(time.time())}"
                request = {
                    "job_id": inferred_id,
                    "kind": "infer_live",
                    "created_at": utc_now(),
                }
            previous_path = self.status_path(request["job_id"])
            previous = read_json_object(previous_path) if previous_path.is_file() else None
            final = build_status(
                request,
                "failed",
                "failed",
                1.0,
                events=[],
                failure_reason=f"{type(exc).__name__}: job could not be completed",
                previous=previous,
            )
            atomic_write_json(previous_path, final)
        finally:
            processing.unlink(missing_ok=True)
            if request is not None and request.get("kind") == "infer_live":
                upload_name = request.get("upload_name")
                if upload_name:
                    (self.settings.uploads_dir / upload_name).unlink(missing_ok=True)
        self.publish_overview()
        return final

    def current_job(self) -> dict[str, Any] | None:
        """Return the newest safe queued/running job summary."""
        LOGGER.info("TuneDemoSupervisor.current_job called")
        candidates = sorted(
            self.settings.jobs_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for status_path in candidates:
            status = read_json_object(status_path)
            if status.get("status") not in {"queued", "running"}:
                continue
            return {
                key: status.get(key)
                for key in (
                    "job_id",
                    "kind",
                    "status",
                    "stage",
                    "progress",
                    "created_at",
                    "started_at",
                    "completed_at",
                )
            }
        return None

    def corpus_publication(self) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Validate prepared files and build exact backend corpus metadata."""
        LOGGER.info("TuneDemoSupervisor.corpus_publication called")
        unavailable = {
            "ready": False,
            "status": "unavailable",
            "input_mode": None,
            "model_id": None,
            "sample_counts": {"total": 0, "train": 0, "holdout": 0},
            "language_counts": {},
            "source_corpus_sha256": None,
            "dataset_manifest_sha256": None,
        }
        dataset_path = self.settings.prepared_dir / "dataset_manifest.json"
        if not dataset_path.is_file():
            return unavailable, None
        try:
            dataset = read_json_object(dataset_path)
            validate_dataset_files(
                dataset,
                self.settings.prepared_dir / "train.jsonl",
                self.settings.prepared_dir / "holdout.jsonl",
            )
            manifest_hash = sha256_file(dataset_path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {**unavailable, "status": "invalid"}, None
        publication = {
            "ready": (
                dataset.get("status") == "frozen"
                and isinstance(dataset.get("source_corpus_sha256"), str)
                and SHA256_PATTERN.fullmatch(dataset["source_corpus_sha256"]) is not None
            ),
            "status": bounded_text(dataset.get("status", "invalid"), 40),
            "input_mode": bounded_text(dataset.get("input_mode", ""), 20) or None,
            "model_id": bounded_text(dataset.get("model_id", ""), 160) or None,
            "sample_counts": safe_sample_counts(dataset.get("sample_counts")),
            "language_counts": safe_aggregate_mapping(dataset.get("language_counts")),
            "source_corpus_sha256": dataset.get("source_corpus_sha256"),
            "dataset_manifest_sha256": manifest_hash,
        }
        return publication, dataset

    def full_artifact_publication(
        self,
        dataset: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Validate full-adapter identity and publish backend artifact metadata."""
        LOGGER.info(
            "TuneDemoSupervisor.full_artifact_publication called dataset_ready=%s",
            dataset is not None,
        )
        unavailable = {
            "available": False,
            "compatible": False,
            "status": "unavailable",
            "profile": None,
            "model_id": None,
            "input_mode": None,
            "sample_counts": {"total": 0, "train": 0, "holdout": 0},
            "language_counts": {},
            "lora_rank": None,
            "completed_steps": None,
            "final_loss": None,
            "duration_seconds": None,
            "peak_vram_gib": None,
            "source_corpus_sha256": None,
            "adapter_sha256": None,
            "created_at": None,
        }
        if not self.settings.artifact_manifest.is_file() or not self.settings.full_adapter.is_dir():
            return unavailable
        try:
            artifact = read_json_object(self.settings.artifact_manifest)
        except (OSError, ValueError, json.JSONDecodeError):
            return {**unavailable, "status": "invalid"}
        training = artifact.get("training") if isinstance(artifact.get("training"), dict) else {}
        metrics_path = self.settings.full_adapter.parent / "training_metrics.json"
        try:
            metrics = read_json_object(metrics_path) if metrics_path.is_file() else {}
        except (OSError, ValueError, json.JSONDecodeError):
            metrics = {}
        trainer_metrics = (
            metrics.get("trainer_metrics")
            if isinstance(metrics.get("trainer_metrics"), dict)
            else {}
        )
        compatible = False
        if dataset is not None:
            try:
                validate_artifact_compatibility(dataset, artifact, self.settings.full_adapter)
                if artifact.get("dataset_manifest_sha256") != sha256_file(
                    self.settings.prepared_dir / "dataset_manifest.json"
                ):
                    raise ValueError("artifact dataset manifest hash mismatch")
                if (
                    not isinstance(artifact.get("source_corpus_sha256"), str)
                    or SHA256_PATTERN.fullmatch(artifact["source_corpus_sha256"]) is None
                ):
                    raise ValueError("artifact source corpus hash is invalid")
            except (OSError, TypeError, ValueError):
                compatible = False
            else:
                compatible = True
        actual_adapter_hash = None
        try:
            actual_adapter_hash = sha256_directory(self.settings.full_adapter)
        except (OSError, ValueError):
            pass
        publication = {
            "available": artifact.get("status") == "completed" and actual_adapter_hash is not None,
            "compatible": compatible,
            "status": bounded_text(artifact.get("status", "invalid"), 40),
            "profile": bounded_text(training.get("profile", metrics.get("profile", "")), 20)
            or None,
            "model_id": bounded_text(artifact.get("model_id", ""), 160) or None,
            "input_mode": bounded_text(
                metrics.get("input_mode", dataset.get("input_mode", "") if dataset else ""),
                20,
            )
            or None,
            "sample_counts": safe_sample_counts(artifact.get("sample_counts")),
            "language_counts": safe_aggregate_mapping(artifact.get("language_counts")),
            "lora_rank": (
                artifact.get("lora", {}).get("rank")
                if isinstance(artifact.get("lora"), dict)
                else None
            ),
            "completed_steps": trainer_metrics.get(
                "global_step",
                training.get(
                    "completed_steps",
                    metrics.get("completed_steps", training.get("max_steps")),
                ),
            ),
            "final_loss": trainer_metrics.get(
                "train_loss",
                training.get("final_loss", metrics.get("final_loss")),
            ),
            "duration_seconds": training.get(
                "duration_seconds",
                metrics.get("duration_seconds"),
            ),
            "peak_vram_gib": training.get("peak_vram_gib", metrics.get("peak_vram_gib")),
            "source_corpus_sha256": artifact.get("source_corpus_sha256"),
            "adapter_sha256": actual_adapter_hash,
            "created_at": artifact.get("created_at"),
        }
        return safe_artifact_payload(publication) or unavailable

    def smoke_artifact_publication(self) -> dict[str, Any] | None:
        """Return the newest completed smoke training proof, when present."""
        LOGGER.info("TuneDemoSupervisor.smoke_artifact_publication called")
        candidates = sorted(
            self.settings.jobs_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for status_path in candidates:
            status = read_json_object(status_path)
            if status.get("status") != "completed" or status.get("kind") != "train_smoke":
                continue
            result = status.get("result")
            proof = result.get("training_proof") if isinstance(result, dict) else None
            return safe_artifact_payload(proof)
        return None

    def approved_publications(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Cross-reference operator approvals and atomically publish approved audio."""
        LOGGER.info(
            "TuneDemoSupervisor.approved_publications called approved_count=%d",
            len(self.settings.approved_sample_ids),
        )
        if not self.settings.approved_sample_ids or not self.settings.approved_predictions.is_file():
            self._remove_unapproved_audio(set())
            return [], []
        holdout_rows = self._read_jsonl(self.settings.prepared_dir / "holdout.jsonl")
        prediction_rows = self._read_jsonl(self.settings.approved_predictions)
        holdout = {
            str(row.get("utterance_id")): row
            for row in holdout_rows
            if row.get("utterance_id") in self.settings.approved_sample_ids
        }
        predictions = {
            str(row.get("sample_id", row.get("utterance_id"))): row
            for row in prediction_rows
            if row.get("sample_id", row.get("utterance_id"))
            in self.settings.approved_sample_ids
        }
        samples: list[dict[str, Any]] = []
        comparisons: list[dict[str, Any]] = []
        approved_names: set[str] = set()
        for sample_id in sorted(self.settings.approved_sample_ids)[:MAX_PREDICTIONS]:
            row = holdout.get(sample_id)
            prediction = predictions.get(sample_id)
            if row is None or prediction is None:
                continue
            audio_path = self._holdout_audio_path(row)
            if audio_path is None:
                continue
            audio_name = f"{sample_id}{audio_path.suffix.lower()}"
            destination = self.settings.published_audio_dir / audio_name
            self._atomic_copy(audio_path, destination)
            approved_names.add(audio_name)
            target = bounded_text(row.get("target", prediction.get("target", "")))
            samples.append(
                {
                    "approved": True,
                    "sample_id": sample_id,
                    "native_language": bounded_text(
                        row.get("native_lang_tag", "Unknown"),
                        MAX_LANGUAGE_CHARS,
                    ),
                    "target": target,
                    "audio_name": audio_name,
                }
            )
            comparisons.append(
                {
                    "approved": True,
                    "sample_id": sample_id,
                    "target": target,
                    "base_output": bounded_text(
                        prediction.get("base_output", prediction.get("base", "")),
                        2000,
                    ),
                    "tuned_output": bounded_text(
                        prediction.get("tuned_output", prediction.get("tuned", "")),
                        2000,
                    ),
                }
            )
        self._remove_unapproved_audio(approved_names)
        return samples, comparisons

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        """Read object-only JSONL from one operator-configured local file."""
        LOGGER.info("_read_jsonl called file_name=%s exists=%s", path.name, path.is_file())
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows

    def _holdout_audio_path(self, row: dict[str, Any]) -> Path | None:
        """Resolve an approved holdout row's existing non-symlink audio file."""
        LOGGER.info("_holdout_audio_path called sample_id=%s", row.get("utterance_id"))
        messages = row.get("messages")
        try:
            audio_item = messages[0]["content"][0]
            value = audio_item["audio"]
        except (IndexError, KeyError, TypeError):
            return None
        if not isinstance(audio_item, dict) or not isinstance(value, str):
            return None
        path = Path(value)
        if (
            row.get("input_mode") != "audio"
            or audio_item.get("type") != "audio"
            or not path.is_absolute()
            or not path.is_file()
            or path.is_symlink()
            or path.suffix.lower() not in ALLOWED_UPLOAD_SUFFIXES
        ):
            return None
        return path

    def _atomic_copy(self, source: Path, destination: Path) -> None:
        """Copy approved audio through a sibling temporary file and replace."""
        LOGGER.info(
            "_atomic_copy called source_name=%s destination_name=%s",
            source.name,
            destination.name,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as temporary:
            with source.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, destination)

    def _remove_unapproved_audio(self, approved_names: set[str]) -> None:
        """Remove published regular audio files absent from the current approval set."""
        LOGGER.info("_remove_unapproved_audio called approved_count=%d", len(approved_names))
        if not self.settings.published_audio_dir.is_dir():
            return
        for path in self.settings.published_audio_dir.iterdir():
            if path.is_file() and not path.is_symlink() and path.name not in approved_names:
                path.unlink()

    def publish_overview(self) -> dict[str, Any]:
        """Publish the exact backend overview protocol with a fresh heartbeat."""
        LOGGER.info("TuneDemoSupervisor.publish_overview called")
        current = self.current_job()
        corpus, dataset = self.corpus_publication()
        samples, comparisons = self.approved_publications()
        overview: dict[str, Any] = {
            "supervisor": {
                "status": "running" if current is not None else "idle",
                "heartbeat_at": utc_now(),
                "message": "GPU job in progress" if current is not None else "Supervisor ready",
            },
            "corpus": corpus,
            "smoke_artifact": self.smoke_artifact_publication(),
            "full_artifact": self.full_artifact_publication(dataset),
            "heldout_samples": samples,
            "heldout_comparisons": comparisons,
            "current_job": current,
        }
        atomic_write_json(self.settings.published_dir / "overview.json", overview)
        return overview

    def run_once(self) -> bool:
        """Perform cleanup, recovery, and at most one queued job."""
        LOGGER.info("TuneDemoSupervisor.run_once called")
        self.recover_stale_processing()
        self.delete_expired_uploads()
        claimed = self.claim_next()
        if claimed is None:
            self.publish_overview()
            return False
        self.process_claim(claimed)
        return True


def dry_run(settings: SupervisorSettings) -> int:
    """Print a non-mutating supervisor rehearsal with only fixed command kinds."""
    LOGGER.info("dry_run called runtime_name=%s", settings.runtime_root.name)
    queued = list(settings.requests_dir.glob("*.json")) if settings.requests_dir.is_dir() else []
    valid = 0
    for request_path in queued:
        try:
            validate_request(read_json_object(request_path), request_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        valid += 1
    print(
        "DRY RUN OK: "
        f"queued={len(queued)} valid={valid} supported={','.join(sorted(SUPPORTED_KINDS))} "
        "commands=fixed preflight=tune.preflight--json full_training=disabled"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Create the host supervisor command-line parser."""
    LOGGER.info("build_parser called")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--once", action="store_true", help="Run one polling iteration and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Rehearse without writes or commands.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the single-instance host supervisor until interrupted or once."""
    LOGGER.info("main called argv_provided=%s", argv is not None)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    settings = load_settings(args.runtime_root)
    if args.dry_run:
        return dry_run(settings)
    supervisor = TuneDemoSupervisor(settings)
    supervisor.initialize()
    lock_path = settings.runtime_root / ".supervisor.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit("another tune demo supervisor is already running") from exc
        if args.once:
            supervisor.run_once()
            return 0
        while True:
            supervisor.run_once()
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
