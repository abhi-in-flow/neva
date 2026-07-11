"""Deterministic valid WAV fixtures for bounded upload scenarios.

Produces short in-memory PCM tones that pass container, duration, and silence
checks without representing real speech. Fixtures never touch disk or ffmpeg
and must only be uploaded to an attested isolated target.
"""

from __future__ import annotations

import io
import logging
import math
import struct
import wave

LOGGER = logging.getLogger(__name__)

FIXTURE_CONTENT_TYPE = "audio/wav"
FIXTURE_DURATION_S = 1.25
FIXTURE_SAMPLE_RATE_HZ = 16_000
FIXTURE_AMPLITUDE = 12_000
FIXTURE_BASE_FREQUENCY_HZ = 440
FIXTURE_MAX_BYTES = 64 * 1024


def fake_audio_bytes(
    client_id: int,
    *,
    duration_s: float = FIXTURE_DURATION_S,
) -> tuple[bytes, str]:
    """Return a deterministic valid mono PCM WAV tone.

    Args:
        client_id: Simulated client identifier mixed into the payload.
        duration_s: Fixture duration constrained to one-to-two seconds.

    Returns:
        Tuple of ``(payload, content_type)`` suitable for multipart upload tests.

    Raises:
        ValueError: When duration is outside the bounded accepted range or the
            generated fixture exceeds its fixed size budget.

    Side effects:
        None.
    """
    LOGGER.info(
        "fake_audio_bytes called client_id=%s duration_s=%s",
        client_id,
        duration_s,
    )
    if duration_s < 1.0 or duration_s > 2.0:
        raise ValueError("WAV fixture duration must be between 1 and 2 seconds")
    frame_count = round(FIXTURE_SAMPLE_RATE_HZ * duration_s)
    frequency_hz = FIXTURE_BASE_FREQUENCY_HZ + client_id % 20
    samples = [
        round(
            FIXTURE_AMPLITUDE
            * math.sin(2.0 * math.pi * frequency_hz * frame / FIXTURE_SAMPLE_RATE_HZ)
        )
        for frame in range(frame_count)
    ]
    pcm = struct.pack(f"<{frame_count}h", *samples)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(FIXTURE_SAMPLE_RATE_HZ)
        wav_file.writeframes(pcm)
    payload = buffer.getvalue()
    if len(payload) > FIXTURE_MAX_BYTES:
        raise ValueError("WAV fixture exceeded bounded size")
    return payload, FIXTURE_CONTENT_TYPE
