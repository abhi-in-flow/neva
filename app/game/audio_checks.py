"""Fast inline audio acceptance checks using ffprobe/ffmpeg.

Performs only duration and silence (mean volume) checks at upload time. Gemini
triage is intentionally out of scope and runs asynchronously via the ``triage``
job. Subprocess output is parsed for metadata; raw audio bytes are never logged.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.game.config import (
    REASON_BAD_AUDIO,
    REASON_TOO_LONG,
    REASON_TOO_QUIET,
    REASON_TOO_SHORT,
    GameFeatureConfig,
    get_game_config,
)

logger = logging.getLogger(__name__)

_MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*([-\d.]+)\s*dB")


@dataclass(frozen=True)
class AudioCheckResult:
    """Outcome of inline duration/silence validation.

    Attributes:
        accepted: Whether the clip may be stored and triaged.
        duration_s: Measured duration when probing succeeded.
        mean_volume_db: ffmpeg volumedetect mean volume when available.
        reason: Playful re-record copy when ``accepted`` is False.
    """

    accepted: bool
    duration_s: float | None = None
    mean_volume_db: float | None = None
    reason: str | None = None


def probe_duration_seconds(path: Path, timeout_s: float) -> float:
    """Read container duration via ffprobe.

    Args:
        path: Absolute path to the uploaded audio file.
        timeout_s: Subprocess timeout in seconds.

    Returns:
        Duration in seconds as reported by ffprobe.

    Side effects:
        Spawns ``ffprobe``. Raises ``RuntimeError`` on failure.
    """
    logger.info(
        "probe_duration_seconds called path_name=%s timeout_s=%s",
        path.name,
        timeout_s,
    )
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if completed.returncode != 0:
        logger.info(
            "probe_duration_seconds failed returncode=%s stderr_len=%s",
            completed.returncode,
            len(completed.stderr or ""),
        )
        raise RuntimeError("ffprobe duration failed")
    duration = float(completed.stdout.strip())
    logger.info("probe_duration_seconds completed duration_s=%s", duration)
    return duration


def probe_mean_volume_db(path: Path, timeout_s: float) -> float:
    """Estimate mean volume in dB via ffmpeg ``volumedetect``.

    Args:
        path: Absolute path to the uploaded audio file.
        timeout_s: Subprocess timeout in seconds.

    Returns:
        Mean volume in dBFS.

    Side effects:
        Spawns ``ffmpeg``. Raises ``RuntimeError`` when mean volume cannot be
        parsed.
    """
    logger.info(
        "probe_mean_volume_db called path_name=%s timeout_s=%s",
        path.name,
        timeout_s,
    )
    cmd = [
        "ffmpeg",
        "-i",
        str(path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    # volumedetect writes to stderr even on success.
    match = _MEAN_VOLUME_RE.search(completed.stderr or "")
    if not match:
        logger.info(
            "probe_mean_volume_db parse_failed returncode=%s stderr_len=%s",
            completed.returncode,
            len(completed.stderr or ""),
        )
        raise RuntimeError("ffmpeg volumedetect failed")
    mean_db = float(match.group(1))
    logger.info("probe_mean_volume_db completed mean_volume_db=%s", mean_db)
    return mean_db


def check_audio_file(
    path: Path,
    *,
    byte_length: int,
    config: GameFeatureConfig | None = None,
) -> AudioCheckResult:
    """Run fast duration and silence checks on an uploaded clip.

    Args:
        path: Absolute path to the saved upload (server-named).
        byte_length: Declared upload size for logging and size gate.
        config: Optional feature config; defaults to ``get_game_config()``.

    Returns:
        ``AudioCheckResult`` with acceptance flag and optional re-record reason.
        Does not delete the file; callers decide cleanup on rejection.

    Side effects:
        Invokes ffprobe/ffmpeg subprocesses. Logs metadata only.
    """
    cfg = config or get_game_config()
    logger.info(
        "check_audio_file called path_name=%s byte_length=%s",
        path.name,
        byte_length,
    )
    try:
        duration_s = probe_duration_seconds(path, cfg.ffprobe_timeout_s)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        logger.info("check_audio_file duration_probe_failed path_name=%s", path.name)
        return AudioCheckResult(accepted=False, reason=REASON_BAD_AUDIO)

    if duration_s < cfg.audio_min_duration_s:
        logger.info("check_audio_file rejected too_short duration_s=%s", duration_s)
        return AudioCheckResult(
            accepted=False,
            duration_s=duration_s,
            reason=REASON_TOO_SHORT,
        )
    if duration_s > cfg.audio_max_duration_s:
        logger.info("check_audio_file rejected too_long duration_s=%s", duration_s)
        return AudioCheckResult(
            accepted=False,
            duration_s=duration_s,
            reason=REASON_TOO_LONG,
        )

    try:
        mean_db = probe_mean_volume_db(path, cfg.ffmpeg_timeout_s)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        logger.info("check_audio_file volume_probe_failed path_name=%s", path.name)
        return AudioCheckResult(
            accepted=False,
            duration_s=duration_s,
            reason=REASON_BAD_AUDIO,
        )

    if mean_db < cfg.silence_mean_volume_db:
        logger.info(
            "check_audio_file rejected silence mean_volume_db=%s",
            mean_db,
        )
        return AudioCheckResult(
            accepted=False,
            duration_s=duration_s,
            mean_volume_db=mean_db,
            reason=REASON_TOO_QUIET,
        )

    logger.info(
        "check_audio_file accepted duration_s=%s mean_volume_db=%s",
        duration_s,
        mean_db,
    )
    return AudioCheckResult(
        accepted=True,
        duration_s=duration_s,
        mean_volume_db=mean_db,
    )
