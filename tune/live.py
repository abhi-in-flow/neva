"""Prepare one temporary live-audio row for isolated Gemma comparison.

Live recordings are normalized into a caller-owned temporary directory and
represented with the same audio-first conversation shape as prepared holdout
rows. They are never appended to the corpus or training split. This module only
invokes fixed ffmpeg arguments and has no application or GPU dependencies.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from collections.abc import Callable, Sequence
from typing import Any

from tune.config import TuneConfig
from tune.events import bounded_text

LOGGER = logging.getLogger(__name__)
LIVE_UTTERANCE_ID = "temporary-live-demo"
LIVE_TARGET = "(live target not known)"
LIVE_INFERENCE_PROMPT = (
    "What does the attached {native_language} speech describe? "
    "Answer with a short English phrase."
)
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def normalize_live_audio(source: Path, destination: Path, config: TuneConfig) -> None:
    """Normalize an existing temporary recording to 16 kHz mono FLAC."""
    LOGGER.info(
        "normalize_live_audio called source_name=%s destination_name=%s",
        source.name,
        destination.name,
    )
    if not source.is_file():
        raise ValueError("temporary live recording does not exist")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is unavailable for live audio normalization")
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "flac",
            str(destination),
        ],
        check=False,
        capture_output=True,
        timeout=config.audio_tool_timeout_seconds,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError("temporary live audio normalization failed")


def probe_live_audio_duration(
    audio_path: Path,
    config: TuneConfig,
    *,
    runner: CommandRunner = subprocess.run,
) -> float:
    """Read normalized duration with fixed ffprobe args and enforce 1–8 seconds."""
    LOGGER.info(
        "probe_live_audio_duration called audio_name=%s minimum=%s maximum=%s",
        audio_path.name,
        config.live_audio_min_seconds,
        config.live_audio_max_seconds,
    )
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe is unavailable for live audio validation")
    command: Sequence[str] = (
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    )
    result = runner(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=config.audio_tool_timeout_seconds,
        shell=False,
    )
    if result.returncode != 0:
        raise ValueError("temporary live audio duration could not be read")
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise ValueError("temporary live audio duration is invalid") from exc
    if not config.live_audio_min_seconds <= duration <= config.live_audio_max_seconds:
        raise ValueError(
            "temporary live audio duration must be between "
            f"{config.live_audio_min_seconds:g} and {config.live_audio_max_seconds:g} seconds"
        )
    return duration


def build_live_row(
    audio_path: Path,
    native_language: str,
    config: TuneConfig,
) -> dict[str, Any]:
    """Build one bounded inference-only conversation outside the corpus."""
    LOGGER.info(
        "build_live_row called audio_name=%s native_language_length=%d",
        audio_path.name,
        len(native_language),
    )
    language = bounded_text(native_language, config.native_language_chars)
    if not language:
        raise ValueError("live source language must not be empty")
    instruction = LIVE_INFERENCE_PROMPT.format(native_language=language)
    return {
        "utterance_id": LIVE_UTTERANCE_ID,
        "native_lang_tag": language,
        "target": LIVE_TARGET,
        "input_mode": "audio",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "audio", "audio": str(audio_path.resolve())},
                    {"type": "text", "text": instruction},
                ],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": LIVE_TARGET}],
            },
        ],
    }
