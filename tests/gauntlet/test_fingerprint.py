"""Acoustic fingerprint tests using ffmpeg and stdlib-only envelope logic.

Exact FLAC-byte hashes are insufficient: re-encoding the same PCM must keep the
content hash stable. Envelope near-matching is exercised for small silence
prefixes. Tests skip cleanly when ffmpeg cannot encode fixtures.
"""

from __future__ import annotations

import math
import struct
import subprocess
from pathlib import Path

import pytest

from worker.config import GauntletLimits
from worker.fingerprint import (
    AcousticFingerprint,
    compute_acoustic_fingerprint,
    encode_envelope,
    envelopes_near_duplicate,
    fingerprints_match,
)


def _sine_pcm(*, seconds: float = 1.0, sample_rate: int = 16_000, freq: float = 220.0) -> bytes:
    """Synthesize a deterministic mono s16le sine wave for fingerprint fixtures."""
    total = int(seconds * sample_rate)
    samples = [
        int(16000 * math.sin(2 * math.pi * freq * (index / sample_rate)))
        for index in range(total)
    ]
    return struct.pack("<" + "h" * total, *samples)


def _write_flac(path: Path, pcm: bytes, *, sample_rate: int = 16_000, compression: int = 5) -> None:
    """Encode PCM bytes to FLAC through ffmpeg without a shell."""
    path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-compression_level",
            str(compression),
            str(path),
        ],
        input=pcm,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        pytest.skip(f"ffmpeg flac encode unavailable: {process.stderr[-200:]!r}")


@pytest.mark.asyncio
async def test_reencoded_flac_shares_content_hash(tmp_path: Path) -> None:
    """Re-encoding identical PCM must not change the acoustic content hash."""
    pcm = _sine_pcm()
    first = tmp_path / "a.flac"
    second = tmp_path / "b.flac"
    _write_flac(first, pcm, compression=0)
    _write_flac(second, pcm, compression=8)
    assert first.read_bytes() != second.read_bytes()
    limits = GauntletLimits()
    left = await compute_acoustic_fingerprint(first, limits)
    right = await compute_acoustic_fingerprint(second, limits)
    assert left.content_hash == right.content_hash
    assert fingerprints_match(
        left,
        right,
        max_shift_frames=limits.fingerprint_max_shift_frames,
        near_distance_ratio=limits.fingerprint_near_distance_ratio,
    )


@pytest.mark.asyncio
async def test_time_shifted_audio_matches_via_envelope(tmp_path: Path) -> None:
    """A short leading silence shift should still near-match under envelope compare."""
    sample_rate = 16_000
    body = _sine_pcm(seconds=0.8, sample_rate=sample_rate)
    silence = b"\x00\x00" * int(0.05 * sample_rate)  # 50ms ~ one frame
    original = tmp_path / "original.flac"
    shifted = tmp_path / "shifted.flac"
    _write_flac(original, body)
    _write_flac(shifted, silence + body)
    limits = GauntletLimits(fingerprint_max_shift_frames=8, fingerprint_near_distance_ratio=0.2)
    left = await compute_acoustic_fingerprint(original, limits)
    right = await compute_acoustic_fingerprint(shifted, limits)
    assert left.content_hash != right.content_hash
    assert fingerprints_match(
        left,
        right,
        max_shift_frames=limits.fingerprint_max_shift_frames,
        near_distance_ratio=limits.fingerprint_near_distance_ratio,
    )


def test_unrelated_envelopes_do_not_match() -> None:
    """Distinct envelopes must stay non-duplicates under the configured budget."""
    left = AcousticFingerprint(content_hash="a" * 64, envelope=(1, 2, 3, 4, 5, 6), frame_ms=50)
    right = AcousticFingerprint(content_hash="b" * 64, envelope=(15, 14, 13, 12, 11, 10), frame_ms=50)
    assert (
        fingerprints_match(
            left,
            right,
            max_shift_frames=2,
            near_distance_ratio=0.12,
        )
        is False
    )
    assert envelopes_near_duplicate(left.envelope, right.envelope, max_shift_frames=2, near_distance_ratio=0.12) is False


def test_encode_envelope_round_trip() -> None:
    """Envelope serialization must round-trip for turn.quality persistence."""
    envelope = (0, 1, 10, 15)
    assert encode_envelope(envelope) == "01af"
    from worker.fingerprint import decode_envelope

    assert decode_envelope("01af") == envelope


@pytest.mark.asyncio
async def test_exact_same_file_fingerprint_stable(tmp_path: Path) -> None:
    """Hashing the same archival FLAC twice must be byte-stable."""
    path = tmp_path / "same.flac"
    _write_flac(path, _sine_pcm(seconds=0.4))
    limits = GauntletLimits()
    first = await compute_acoustic_fingerprint(path, limits)
    second = await compute_acoustic_fingerprint(path, limits)
    assert first == second
