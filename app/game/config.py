"""Centralized game-core feature configuration.

Holds thresholds, scoring constants, media limits, and playful re-record copy
used by matchmaking, audio acceptance, turn timing, and state composition.
Session ``rounds_cap`` remains in ``app.config.Settings``; this module covers
game-owned knobs only so feature code never scatters magic numbers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GameFeatureConfig:
    """Immutable knobs for game-core behavior.

    Attributes:
        audio_min_duration_s: Minimum accepted utterance length in seconds.
        audio_max_duration_s: Maximum accepted utterance length in seconds.
        silence_mean_volume_db: ffmpeg ``volumedetect`` mean_volume below which
            audio is treated as silence.
        max_audio_bytes: Reject uploads larger than this many bytes.
        max_guess_attempts: Wrong guesses allowed before ``unclear``.
        points_per_validation: Points awarded to each participant on validate.
        leaderboard_default_top: Default row count for leaderboard queries.
        leaderboard_state_top: Compact leaderboard embedded in ``/api/state``.
        result_hold_seconds: Keep ``round_result`` phase this long after the
            next turn is created (uses next-turn ``created_at`` as score time).
        turn_deadline_seconds: Optional per-turn deadline offset from turn
            creation; ``None`` disables deadlines.
        ffprobe_timeout_s: Subprocess timeout for duration probes.
        ffmpeg_timeout_s: Subprocess timeout for volume probes.
    """

    audio_min_duration_s: float = 1.0
    audio_max_duration_s: float = 8.0
    silence_mean_volume_db: float = -40.0
    max_audio_bytes: int = 5_000_000
    max_guess_attempts: int = 2
    points_per_validation: int = 10
    leaderboard_default_top: int = 15
    leaderboard_state_top: int = 5
    result_hold_seconds: float = 2.5
    turn_deadline_seconds: int | None = 90
    ffprobe_timeout_s: float = 5.0
    ffmpeg_timeout_s: float = 10.0


_CONFIG = GameFeatureConfig()


def get_game_config() -> GameFeatureConfig:
    """Return the process-wide game feature configuration.

    Returns:
        The frozen ``GameFeatureConfig`` singleton used by game services.

    Side effects:
        Logs the call at INFO with non-secret configuration values.
    """
    logger.info(
        "get_game_config called audio_min=%s audio_max=%s points=%s "
        "max_attempts=%s result_hold=%s",
        _CONFIG.audio_min_duration_s,
        _CONFIG.audio_max_duration_s,
        _CONFIG.points_per_validation,
        _CONFIG.max_guess_attempts,
        _CONFIG.result_hold_seconds,
    )
    return _CONFIG


# Playful re-record reasons returned to the client (never technical errors).
REASON_TOO_SHORT = "Didn't catch that — hold a little longer! 🔊"
REASON_TOO_LONG = "Whoa, novel-length clue — keep it under 8 seconds!"
REASON_TOO_QUIET = "Didn't catch that — louder! 🔊"
REASON_TOO_LARGE = "That clip is too heavy for the venue Wi-Fi — try again!"
REASON_BAD_AUDIO = "Didn't catch that — try one more time! 🔊"
