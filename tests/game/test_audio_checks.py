"""Unit tests for inline audio acceptance helpers.

Uses temporary files and subprocess probes when ffmpeg/ffprobe are available.
A synthetic silent-rejection path is covered via monkeypatched probes so the
suite stays hermetic without live data mutation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

from app.game.audio_checks import AudioCheckResult, check_audio_file
from app.game.config import (
    REASON_TOO_LONG,
    REASON_TOO_QUIET,
    REASON_TOO_SHORT,
    GameFeatureConfig,
)

logger = logging.getLogger(__name__)


def test_check_audio_rejects_too_short(tmp_path: Path) -> None:
    """Reject clips below the minimum duration.

    Args:
        tmp_path: Temporary directory for a fake audio file.
    """
    logger.info("test_check_audio_rejects_too_short called")
    path = tmp_path / "short.webm"
    path.write_bytes(b"x")
    cfg = GameFeatureConfig(audio_min_duration_s=1.0, audio_max_duration_s=8.0)
    with (
        patch("app.game.audio_checks.probe_duration_seconds", return_value=0.4),
        patch("app.game.audio_checks.probe_mean_volume_db", return_value=-10.0),
    ):
        result = check_audio_file(path, byte_length=1, config=cfg)
    assert result == AudioCheckResult(
        accepted=False,
        duration_s=0.4,
        reason=REASON_TOO_SHORT,
    )
    logger.info("test_check_audio_rejects_too_short completed")


def test_check_audio_rejects_too_long(tmp_path: Path) -> None:
    """Reject clips above the maximum duration."""
    logger.info("test_check_audio_rejects_too_long called")
    path = tmp_path / "long.webm"
    path.write_bytes(b"x")
    cfg = GameFeatureConfig()
    with (
        patch("app.game.audio_checks.probe_duration_seconds", return_value=12.0),
        patch("app.game.audio_checks.probe_mean_volume_db", return_value=-10.0),
    ):
        result = check_audio_file(path, byte_length=1, config=cfg)
    assert result.accepted is False
    assert result.reason == REASON_TOO_LONG
    logger.info("test_check_audio_rejects_too_long completed")


def test_check_audio_rejects_silence(tmp_path: Path) -> None:
    """Reject clips whose mean volume is below the silence threshold."""
    logger.info("test_check_audio_rejects_silence called")
    path = tmp_path / "quiet.webm"
    path.write_bytes(b"x")
    cfg = GameFeatureConfig(silence_mean_volume_db=-40.0)
    with (
        patch("app.game.audio_checks.probe_duration_seconds", return_value=3.0),
        patch("app.game.audio_checks.probe_mean_volume_db", return_value=-55.0),
    ):
        result = check_audio_file(path, byte_length=1, config=cfg)
    assert result.accepted is False
    assert result.reason == REASON_TOO_QUIET
    logger.info("test_check_audio_rejects_silence completed")


def test_check_audio_accepts_healthy_clip(tmp_path: Path) -> None:
    """Accept a mid-duration, non-silent clip."""
    logger.info("test_check_audio_accepts_healthy_clip called")
    path = tmp_path / "ok.webm"
    path.write_bytes(b"x")
    cfg = GameFeatureConfig()
    with (
        patch("app.game.audio_checks.probe_duration_seconds", return_value=3.2),
        patch("app.game.audio_checks.probe_mean_volume_db", return_value=-18.0),
    ):
        result = check_audio_file(path, byte_length=1, config=cfg)
    assert result.accepted is True
    assert result.duration_s == 3.2
    logger.info("test_check_audio_accepts_healthy_clip completed")
