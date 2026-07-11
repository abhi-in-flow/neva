"""Central configuration for the filesystem-backed tune administration bridge.

The Dockerized FastAPI process and the host-side GPU supervisor communicate
only through a bounded runtime tree beneath ``DATA_DIR/tune-demo``. This module
owns every filename, upload limit, content-type decision, retention bound, and
heartbeat threshold used by the API so repository and route code contain no
deployment-specific paths or magic values.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)


def _positive_int(name: str, default: int) -> int:
    """Read one positive integer environment override.

    Args:
        name: Environment variable name.
        default: Positive fallback used when the variable is absent.

    Returns:
        A validated positive integer.

    Raises:
        ValueError: If the configured value is zero, negative, or not numeric.
    """
    raw = os.getenv(name)
    value = int(raw) if raw is not None else default
    logger.info("_positive_int called name=%s overridden=%s", name, raw is not None)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: float) -> float:
    """Read one positive floating-point environment override.

    Args:
        name: Environment variable name.
        default: Positive fallback used when the variable is absent.

    Returns:
        A validated positive float.

    Raises:
        ValueError: If the configured value is zero, negative, or not numeric.
    """
    raw = os.getenv(name)
    value = float(raw) if raw is not None else default
    logger.info("_positive_float called name=%s overridden=%s", name, raw is not None)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class TuneAdminConfig:
    """Describe the API-owned tune-demo filesystem and safety limits."""

    data_dir: Path
    runtime_dir_name: str
    overview_filename: str
    requests_dir_name: str
    jobs_dir_name: str
    uploads_dir_name: str
    published_dir_name: str
    published_audio_dir_name: str
    queue_lock_filename: str
    max_upload_bytes: int
    upload_chunk_bytes: int
    allowed_audio_types: tuple[str, ...]
    audio_type_extensions: tuple[tuple[str, str], ...]
    default_upload_extension: str
    heartbeat_stale_seconds: float
    queue_lock_stale_seconds: float
    max_events: int
    max_samples: int
    max_language_chars: int

    @property
    def runtime_root(self) -> Path:
        """Return the tune-demo root beneath configured ``DATA_DIR``."""
        return self.data_dir / self.runtime_dir_name

    @property
    def requests_dir(self) -> Path:
        """Return the supervisor request queue directory."""
        return self.runtime_root / self.requests_dir_name

    @property
    def jobs_dir(self) -> Path:
        """Return the supervisor job-status directory."""
        return self.runtime_root / self.jobs_dir_name

    @property
    def uploads_dir(self) -> Path:
        """Return the private temporary live-audio upload directory."""
        return self.runtime_root / self.uploads_dir_name

    @property
    def published_dir(self) -> Path:
        """Return the supervisor-owned safe publication directory."""
        return self.runtime_root / self.published_dir_name

    @property
    def published_audio_dir(self) -> Path:
        """Return the confined approved held-out audio directory."""
        return self.published_dir / self.published_audio_dir_name

    @property
    def overview_path(self) -> Path:
        """Return the supervisor-published safe overview file."""
        return self.published_dir / self.overview_filename

    @property
    def queue_lock_path(self) -> Path:
        """Return the API queue serialization lock path."""
        return self.runtime_root / self.queue_lock_filename

    def extension_for_content_type(self, content_type: str) -> str:
        """Return a server-owned extension for one allowed audio type.

        Args:
            content_type: Normalized MIME type already accepted by the API.

        Returns:
            A fixed extension used only in generated private upload names.
        """
        return dict(self.audio_type_extensions).get(
            content_type,
            self.default_upload_extension,
        )


@lru_cache
def get_tune_admin_config() -> TuneAdminConfig:
    """Load and cache tune-admin paths and safety limits.

    Returns:
        Process-stable ``TuneAdminConfig`` derived from application ``DATA_DIR``
        and optional ``TUNE_ADMIN_*`` environment overrides.

    Side effects:
        Reads environment variables on first call and logs only non-secret
        limits and path names.
    """
    settings = get_settings()
    allowed = tuple(
        item.strip().lower()
        for item in os.getenv(
            "TUNE_ADMIN_ALLOWED_AUDIO_TYPES",
            "audio/flac,audio/x-flac,audio/wav,audio/x-wav,audio/webm,"
            "audio/ogg,audio/mp4,audio/mpeg",
        ).split(",")
        if item.strip()
    )
    if not allowed:
        raise ValueError("TUNE_ADMIN_ALLOWED_AUDIO_TYPES must not be empty")
    config = TuneAdminConfig(
        data_dir=settings.data_dir,
        runtime_dir_name="tune-demo",
        overview_filename="overview.json",
        requests_dir_name="requests",
        jobs_dir_name="jobs",
        uploads_dir_name="uploads",
        published_dir_name="published",
        published_audio_dir_name="audio",
        queue_lock_filename=".api-queue.lock",
        max_upload_bytes=_positive_int("TUNE_ADMIN_MAX_UPLOAD_BYTES", 2 * 1024 * 1024),
        upload_chunk_bytes=_positive_int("TUNE_ADMIN_UPLOAD_CHUNK_BYTES", 64 * 1024),
        allowed_audio_types=allowed,
        audio_type_extensions=(
            ("audio/flac", ".flac"),
            ("audio/x-flac", ".flac"),
            ("audio/wav", ".wav"),
            ("audio/x-wav", ".wav"),
            ("audio/webm", ".webm"),
            ("audio/ogg", ".ogg"),
            ("audio/mp4", ".m4a"),
            ("audio/mpeg", ".mp3"),
        ),
        default_upload_extension=".webm",
        heartbeat_stale_seconds=_positive_float(
            "TUNE_ADMIN_HEARTBEAT_STALE_SECONDS",
            30.0,
        ),
        queue_lock_stale_seconds=_positive_float(
            "TUNE_ADMIN_QUEUE_LOCK_STALE_SECONDS",
            10.0,
        ),
        max_events=_positive_int("TUNE_ADMIN_MAX_EVENTS", 100),
        max_samples=_positive_int("TUNE_ADMIN_MAX_SAMPLES", 10),
        max_language_chars=_positive_int("TUNE_ADMIN_MAX_LANGUAGE_CHARS", 80),
    )
    logger.info(
        "get_tune_admin_config called runtime_name=%s max_upload_bytes=%s "
        "allowed_type_count=%s heartbeat_stale_seconds=%s",
        config.runtime_dir_name,
        config.max_upload_bytes,
        len(config.allowed_audio_types),
        config.heartbeat_stale_seconds,
    )
    return config
