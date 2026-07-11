"""Valid in-memory WAV fixture and multipart upload tests."""

from __future__ import annotations

import io
import logging
import math
import struct
import wave

from tools.load.client import ApiSession, LoadClient
from tools.load.config import LoadConfig
from tools.load.fixtures import (
    FIXTURE_CONTENT_TYPE,
    FIXTURE_MAX_BYTES,
    fake_audio_bytes,
)
from tools.load.metrics import MetricsCollector
from tools.load.transport import RecordingTransport

LOGGER = logging.getLogger(__name__)


class CaptureBodyTransport(RecordingTransport):
    """Recording transport that retains the last request body for assertions."""

    def __init__(self) -> None:
        """Initialize the recording transport and body capture."""
        LOGGER.info("CaptureBodyTransport.__init__ called")
        super().__init__()
        self.last_body = b""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout_s: float = 10.0,
    ):
        """Capture the body and delegate to canned endpoint responses.

        Args:
            method: HTTP verb.
            url: Absolute request URL.
            headers: Optional request headers.
            body: Optional request body.
            timeout_s: Request timeout.

        Returns:
            Canned recording response.
        """
        LOGGER.info(
            "CaptureBodyTransport.request called method=%s body_len=%s",
            method,
            len(body or b""),
        )
        self.last_body = body or b""
        return super().request(
            method,
            url,
            headers=headers,
            body=body,
            timeout_s=timeout_s,
        )


def test_fixture_is_valid_bounded_non_silent_wav() -> None:
    """Generate deterministic 1–2 second PCM WAV above silence threshold."""
    LOGGER.info("test_fixture_is_valid_bounded_non_silent_wav called")
    first, content_type = fake_audio_bytes(7)
    second, _ = fake_audio_bytes(7)
    assert first == second
    assert content_type == "audio/wav"
    assert len(first) <= FIXTURE_MAX_BYTES

    with wave.open(io.BytesIO(first), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16_000
        duration_s = wav_file.getnframes() / wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    assert 1.0 <= duration_s <= 2.0
    assert rms > 1_000


def test_upload_uses_wav_filename_and_content_type(safe_config: LoadConfig) -> None:
    """Send the fixture as bounded ``audio/wav`` multipart content."""
    LOGGER.info("test_upload_uses_wav_filename_and_content_type called")
    transport = CaptureBodyTransport()
    session = ApiSession(
        config=safe_config,
        transport=transport,
        metrics=MetricsCollector(),
    )
    client = LoadClient(
        client_id=1,
        nickname="fixture",
        native_lang="as",
        common_langs=["en"],
        token="fixture-token",
    )
    session.upload_fixture_audio(client)
    assert b'filename="fixture-1.wav"' in transport.last_body
    assert f"Content-Type: {FIXTURE_CONTENT_TYPE}".encode() in transport.last_body
