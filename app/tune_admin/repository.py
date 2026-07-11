"""Filesystem repository for Docker-to-host tune-demo coordination.

The repository is the only API module that knows the runtime JSON protocol. It
atomically writes request and queued-job files, reads supervisor publications,
stages bounded temporary uploads, and resolves approved audio under one
published directory. Parsed dictionaries never expose paths directly; shaping
and redaction remain the service layer's responsibility.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID

from app.tune_admin.config import TuneAdminConfig

logger = logging.getLogger(__name__)

_SAMPLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


class TuneAdminRepositoryError(RuntimeError):
    """Signal a safe filesystem or runtime-protocol failure."""


class TuneAdminBusyError(TuneAdminRepositoryError):
    """Signal that a queued or running GPU job already exists."""


def _read_json_object(path: Path) -> dict[str, Any]:
    """Read one UTF-8 JSON object with a path-free error.

    Args:
        path: Runtime JSON file selected by server configuration or UUID.

    Returns:
        Parsed JSON object.

    Raises:
        TuneAdminRepositoryError: If the file is unreadable, malformed, or not
            a JSON object. The exception never contains file contents or paths.
    """
    logger.info("_read_json_object called file_name=%s", path.name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TuneAdminRepositoryError("Tune runtime metadata is unavailable") from exc
    if not isinstance(payload, dict):
        raise TuneAdminRepositoryError("Tune runtime metadata is unavailable")
    return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace one JSON object using a sibling temporary file.

    Args:
        path: Server-constructed destination below the runtime root.
        payload: JSON-safe protocol object.

    Side effects:
        Creates the destination parent, fsyncs a temporary file, and replaces
        the destination atomically.

    Raises:
        TuneAdminRepositoryError: If serialization or filesystem work fails.
    """
    logger.info(
        "_atomic_write_json called file_name=%s key_count=%s",
        path.name,
        len(payload),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    except (OSError, TypeError, ValueError) as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise TuneAdminRepositoryError("Tune runtime write failed") from exc


class TuneAdminRepository:
    """Coordinate safe API reads and writes beneath one tune-demo root."""

    def __init__(self, config: TuneAdminConfig) -> None:
        """Bind immutable paths and limits.

        Args:
            config: Centralized tune-admin runtime configuration.
        """
        self._config = config
        logger.info(
            "TuneAdminRepository initialized runtime_name=%s",
            config.runtime_dir_name,
        )

    def ensure_runtime_directories(self) -> None:
        """Create only API/supervisor protocol directories when needed.

        Side effects:
            Creates the runtime, request, job, upload, published, and published
            audio directories. Existing files are not modified.
        """
        logger.info("ensure_runtime_directories called")
        for directory in (
            self._config.runtime_root,
            self._config.requests_dir,
            self._config.jobs_dir,
            self._config.uploads_dir,
            self._config.published_dir,
            self._config.published_audio_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def read_overview(self) -> dict[str, Any] | None:
        """Read the supervisor-published overview when present.

        Returns:
            Parsed overview object, or ``None`` before any publication exists.

        Raises:
            TuneAdminRepositoryError: If a present publication is malformed.
        """
        logger.info("read_overview called")
        if not self._config.overview_path.is_file():
            return None
        return _read_json_object(self._config.overview_path)

    def read_job(self, job_id: UUID) -> dict[str, Any] | None:
        """Read one UUID-addressed job status.

        Args:
            job_id: Server-validated job UUID.

        Returns:
            Parsed job object or ``None`` when it is unknown.
        """
        logger.info("read_job called job_id=%s", job_id)
        path = self._config.jobs_dir / f"{job_id}.json"
        if not path.is_file():
            return None
        return _read_json_object(path)

    def read_active_job(self) -> dict[str, Any] | None:
        """Return the newest queued/running job object when one exists.

        Returns:
            Parsed active job object or ``None`` when the GPU queue is idle.

        Raises:
            TuneAdminRepositoryError: If a UUID-named candidate is malformed.
        """
        logger.info("read_active_job called")
        if not self._config.jobs_dir.is_dir():
            return None
        candidates = sorted(
            self._config.jobs_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in candidates:
            try:
                UUID(path.stem)
            except ValueError:
                continue
            payload = _read_json_object(path)
            if payload.get("status") in {"queued", "running"}:
                return payload
        return None

    def _active_job_exists(self) -> bool:
        """Return whether any valid job file declares queued or running state.

        Returns:
            ``True`` when a GPU operation is active.

        Raises:
            TuneAdminRepositoryError: If a UUID-named status file is malformed;
            the API fails closed instead of risking concurrent GPU work.
        """
        logger.info("_active_job_exists called")
        if not self._config.jobs_dir.is_dir():
            return False
        for path in self._config.jobs_dir.glob("*.json"):
            try:
                UUID(path.stem)
            except ValueError:
                continue
            payload = _read_json_object(path)
            if payload.get("status") in {"queued", "running"}:
                return True
        return False

    @contextmanager
    def _queue_lock(self) -> Iterator[None]:
        """Serialize busy checks and two-file queue publication.

        Yields:
            Control while this API process owns the exclusive lock file.

        Raises:
            TuneAdminBusyError: If another request currently owns a fresh lock.
            TuneAdminRepositoryError: If the lock cannot be managed safely.

        Side effects:
            Creates and removes a server-owned lock file. A lock older than the
            configured stale threshold is removed before one retry.
        """
        logger.info("_queue_lock called")
        self.ensure_runtime_directories()
        lock_path = self._config.queue_lock_path
        for attempt in range(2):
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                os.write(descriptor, str(os.getpid()).encode("ascii"))
                os.close(descriptor)
                break
            except FileExistsError as exc:
                try:
                    age = time.time() - lock_path.stat().st_mtime
                except OSError as stat_exc:
                    raise TuneAdminRepositoryError("Tune queue lock is unavailable") from stat_exc
                if attempt == 0 and age > self._config.queue_lock_stale_seconds:
                    lock_path.unlink(missing_ok=True)
                    continue
                raise TuneAdminBusyError("Another tune queue operation is in progress") from exc
            except OSError as exc:
                raise TuneAdminRepositoryError("Tune queue lock is unavailable") from exc
        else:
            raise TuneAdminBusyError("Another tune queue operation is in progress")
        try:
            yield
        finally:
            lock_path.unlink(missing_ok=True)

    def enqueue(
        self,
        job_id: UUID,
        request_payload: dict[str, Any],
        job_payload: dict[str, Any],
    ) -> None:
        """Publish one queued status and matching supervisor request atomically.

        Args:
            job_id: Server-generated operation UUID used for both filenames.
            request_payload: Fixed supervisor request protocol object.
            job_payload: Initial queued job protocol object.

        Raises:
            TuneAdminBusyError: If another queued/running job exists.
            TuneAdminRepositoryError: If either atomic write fails.

        Side effects:
            Writes ``jobs/{id}.json`` then ``requests/{id}.json`` under an
            exclusive queue lock. A failure removes both files so a partial API
            transaction is not left visible.
        """
        logger.info(
            "enqueue called job_id=%s kind=%s",
            job_id,
            request_payload.get("kind"),
        )
        job_path = self._config.jobs_dir / f"{job_id}.json"
        request_path = self._config.requests_dir / f"{job_id}.json"
        with self._queue_lock():
            if self._active_job_exists():
                raise TuneAdminBusyError("A tune GPU job is already active")
            try:
                _atomic_write_json(job_path, job_payload)
                _atomic_write_json(request_path, request_payload)
            except TuneAdminRepositoryError:
                request_path.unlink(missing_ok=True)
                job_path.unlink(missing_ok=True)
                raise

    def stage_upload(self, upload_name: str, payload: bytes) -> Path:
        """Atomically stage server-named temporary live audio.

        Args:
            upload_name: Server-generated basename with an approved extension.
            payload: Bounded audio bytes already validated by the service.

        Returns:
            Final private upload path for repository rollback bookkeeping.

        Raises:
            TuneAdminRepositoryError: If the name is not a basename or the
            atomic write fails.

        Side effects:
            Writes a temporary sibling and atomically renames it into uploads.
        """
        logger.info(
            "stage_upload called upload_name=%s byte_length=%s",
            upload_name,
            len(payload),
        )
        if Path(upload_name).name != upload_name:
            raise TuneAdminRepositoryError("Invalid temporary upload name")
        self.ensure_runtime_directories()
        destination = self._config.uploads_dir / upload_name
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self._config.uploads_dir,
                prefix=f".{upload_name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, destination)
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise TuneAdminRepositoryError("Temporary audio staging failed") from exc
        return destination

    def remove_upload(self, upload_name: str) -> None:
        """Delete one server-generated temporary upload during queue rollback.

        Args:
            upload_name: Basename previously generated by the service.

        Side effects:
            Removes the named file only when it resolves directly under the
            configured uploads directory.
        """
        logger.info("remove_upload called upload_name=%s", upload_name)
        if Path(upload_name).name != upload_name:
            return
        (self._config.uploads_dir / upload_name).unlink(missing_ok=True)

    def resolve_published_audio(self, sample_id: str) -> Path | None:
        """Resolve approved held-out audio strictly beneath published audio.

        Args:
            sample_id: Contract-validated public sample identifier.

        Returns:
            Existing confined audio file, or ``None`` for unknown/unavailable
            samples.

        Raises:
            TuneAdminRepositoryError: If publication metadata attempts path
            traversal, uses a symlink, or is malformed.
        """
        logger.info("resolve_published_audio called sample_id=%s", sample_id)
        if not _SAMPLE_ID_PATTERN.fullmatch(sample_id):
            return None
        overview = self.read_overview()
        if overview is None:
            return None
        rows = overview.get("heldout_samples")
        if not isinstance(rows, list):
            raise TuneAdminRepositoryError("Tune sample metadata is unavailable")
        audio_name: str | None = None
        for row in rows:
            if (
                isinstance(row, dict)
                and row.get("sample_id") == sample_id
                and row.get("approved") is True
            ):
                candidate = row.get("audio_name")
                if isinstance(candidate, str):
                    audio_name = candidate
                break
        if audio_name is None or Path(audio_name).name != audio_name:
            return None
        root = self._config.published_audio_dir.resolve()
        path = (root / audio_name).resolve()
        if root not in path.parents or path.is_symlink():
            raise TuneAdminRepositoryError("Published tune audio is unavailable")
        if path.suffix.lower() not in {
            ".flac",
            ".wav",
            ".webm",
            ".ogg",
            ".mp3",
            ".m4a",
        }:
            return None
        return path if path.is_file() else None
