"""In-memory audio fixtures for isolated end-to-end acceptance runs.

Builds PCM WAV clips that pass inline ffprobe/ffmpeg duration and silence checks
without reading or writing repository runtime data.
"""

from __future__ import annotations

import io
import logging
import math
import struct
import wave

LOGGER = logging.getLogger(__name__)

DEFAULT_DURATION_S = 3.0
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_FREQUENCY_HZ = 440.0
DEFAULT_AMPLITUDE = 0.45


def build_valid_wav_bytes(
    *,
    duration_s: float = DEFAULT_DURATION_S,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    frequency_hz: float = DEFAULT_FREQUENCY_HZ,
    amplitude: float = DEFAULT_AMPLITUDE,
) -> bytes:
    """Build an in-memory mono PCM WAV clip loud enough for inline acceptance.

    Args:
        duration_s: Clip length in seconds.
        sample_rate: PCM sample rate in hertz.
        frequency_hz: Sine tone used to avoid silence rejection.
        amplitude: Peak amplitude fraction in ``(0, 1]``.

    Returns:
        Complete WAV file bytes suitable for multipart upload.

    Side effects:
        None. Pure in-memory generation.
    """
    LOGGER.info(
        "build_valid_wav_bytes called duration_s=%s sample_rate=%s frequency_hz=%s "
        "amplitude=%s",
        duration_s,
        sample_rate,
        frequency_hz,
        amplitude,
    )
    sample_count = max(1, int(duration_s * sample_rate))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = bytearray()
        for index in range(sample_count):
            sample = int(amplitude * 32_767 * math.sin(2 * math.pi * frequency_hz * index / sample_rate))
            frames.extend(struct.pack("<h", sample))
        handle.writeframes(frames)
    payload = buffer.getvalue()
    LOGGER.info("build_valid_wav_bytes completed byte_length=%s", len(payload))
    return payload
