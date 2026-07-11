"""Deterministic acoustic fingerprints for per-speaker de-duplication.

Exact FLAC-byte hashes miss re-encoded copies of the same PCM. This module
decodes archival FLAC to signed 16-bit mono PCM via ffmpeg (no shell), then
derives:

1. A content hash over the PCM bytes (stable across FLAC re-encoding).
2. A coarse quantized RMS envelope used for limited time-shift near matching.

Matching claims are intentionally conservative: callers must only assert the
behaviors covered by focused tests. Schema-backed atomic registration is
orchestrator-owned; the repository layer supplies a lock-based fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import struct
from dataclasses import dataclass
from pathlib import Path

from worker.config import GauntletLimits

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AcousticFingerprint:
    """PCM content hash plus a coarse envelope for limited near matching.

    Attributes:
        content_hash: SHA-256 hex digest of decoded s16le mono PCM.
        envelope: Quantized per-frame RMS levels used for shift-tolerant compare.
        frame_ms: Frame duration that produced ``envelope``.
    """

    content_hash: str
    envelope: tuple[int, ...]
    frame_ms: int

    @property
    def dedup_hash(self) -> str:
        """Return the stable identifier stored in ``quality.dedup_hash``."""
        return self.content_hash


async def compute_acoustic_fingerprint(
    flac_path: Path, limits: GauntletLimits
) -> AcousticFingerprint:
    """Decode a FLAC file and compute the deterministic acoustic fingerprint.

    Args:
        flac_path: Existing clean FLAC recording under an isolated or live data root.
        limits: Sample rate, channel, frame, and ffmpeg timeout settings.

    Returns:
        Content hash and quantized energy envelope.

    Raises:
        FileNotFoundError: If the FLAC path is missing.
        RuntimeError: If ffmpeg fails, times out, or yields empty PCM.
    """
    logger.info(
        "compute_acoustic_fingerprint called flac_path=%s bytes=%s sample_rate=%s",
        flac_path,
        flac_path.stat().st_size if flac_path.exists() else None,
        limits.sample_rate_hz,
    )
    pcm = await _decode_pcm(flac_path, limits)
    content_hash = hashlib.sha256(pcm).hexdigest()
    envelope = _quantized_rms_envelope(
        pcm,
        sample_rate_hz=limits.sample_rate_hz,
        frame_ms=limits.fingerprint_frame_ms,
    )
    logger.info(
        "compute_acoustic_fingerprint completed content_hash_prefix=%s envelope_frames=%s",
        content_hash[:12],
        len(envelope),
    )
    return AcousticFingerprint(
        content_hash=content_hash,
        envelope=envelope,
        frame_ms=limits.fingerprint_frame_ms,
    )


def fingerprints_match(
    left: AcousticFingerprint,
    right: AcousticFingerprint,
    *,
    max_shift_frames: int,
    near_distance_ratio: float,
) -> bool:
    """Return whether two fingerprints represent the same utterance content.

    Exact PCM hashes always match. Otherwise a bounded envelope alignment may
    match time-shifted copies when the normalized L1 distance stays under the
    configured ratio. This is not a general near-duplicate detector.

    Args:
        left: First fingerprint.
        right: Second fingerprint.
        max_shift_frames: Maximum absolute frame shift considered.
        near_distance_ratio: Maximum allowed mean absolute envelope distance
            relative to the quantization range (0-15).

    Returns:
        ``True`` when content hashes match or envelopes are near under shift.
    """
    logger.info(
        "fingerprints_match called left_prefix=%s right_prefix=%s max_shift=%s",
        left.content_hash[:12],
        right.content_hash[:12],
        max_shift_frames,
    )
    if left.content_hash == right.content_hash:
        return True
    return envelopes_near_duplicate(
        left.envelope,
        right.envelope,
        max_shift_frames=max_shift_frames,
        near_distance_ratio=near_distance_ratio,
    )


def envelopes_near_duplicate(
    left: tuple[int, ...] | list[int],
    right: tuple[int, ...] | list[int],
    *,
    max_shift_frames: int,
    near_distance_ratio: float,
) -> bool:
    """Compare quantized RMS envelopes under a small absolute time shift.

    Args:
        left: First quantized envelope.
        right: Second quantized envelope.
        max_shift_frames: Inclusive shift search radius in frames.
        near_distance_ratio: Threshold against a 0-15 quantization scale.

    Returns:
        ``True`` when any tested alignment is within the distance budget.
    """
    logger.info(
        "envelopes_near_duplicate called left_frames=%s right_frames=%s max_shift=%s",
        len(left),
        len(right),
        max_shift_frames,
    )
    if not left or not right:
        return False
    max_distance = near_distance_ratio * 15.0
    for shift in range(-max_shift_frames, max_shift_frames + 1):
        distance = _aligned_mean_abs_distance(left, right, shift)
        if distance is not None and distance <= max_distance:
            return True
    return False


def encode_envelope(envelope: tuple[int, ...] | list[int]) -> str:
    """Serialize an envelope for optional JSON persistence and tests.

    Args:
        envelope: Quantized RMS levels in ``0..15``.

    Returns:
        Compact hex nibble string (one character per frame).
    """
    logger.info("encode_envelope called frames=%s", len(envelope))
    return "".join(f"{level:x}" for level in envelope)


def decode_envelope(encoded: str) -> tuple[int, ...]:
    """Parse an envelope previously produced by ``encode_envelope``.

    Args:
        encoded: Hex nibble string.

    Returns:
        Quantized RMS levels.
    """
    logger.info("decode_envelope called chars=%s", len(encoded))
    return tuple(int(char, 16) for char in encoded)


async def _decode_pcm(flac_path: Path, limits: GauntletLimits) -> bytes:
    """Decode archival FLAC to raw s16le mono PCM using ffmpeg.

    Args:
        flac_path: Existing FLAC file.
        limits: Sample-rate, channel, and timeout settings.

    Returns:
        Raw PCM bytes.

    Raises:
        FileNotFoundError: Missing input file.
        RuntimeError: ffmpeg failure, timeout, or empty output.
    """
    logger.info("_decode_pcm called flac_path=%s", flac_path)
    if not flac_path.is_file():
        raise FileNotFoundError(f"flac missing for fingerprint: {flac_path}")
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(flac_path),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(limits.sample_rate_hz),
        "-ac",
        str(limits.channels),
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=limits.ffmpeg_timeout_seconds
        )
    except TimeoutError as error:
        process.kill()
        await process.wait()
        raise RuntimeError("ffmpeg PCM decode timed out") from error
    if process.returncode != 0:
        reason = stderr.decode("utf-8", errors="replace").splitlines()[-1:] or ["unknown"]
        raise RuntimeError(f"ffmpeg PCM decode failed: {reason[0][:240]}")
    if not stdout:
        raise RuntimeError("ffmpeg PCM decode produced empty audio")
    logger.info("_decode_pcm completed pcm_bytes=%s", len(stdout))
    return stdout


def _quantized_rms_envelope(
    pcm: bytes, *, sample_rate_hz: int, frame_ms: int
) -> tuple[int, ...]:
    """Build a 4-bit quantized RMS envelope over fixed PCM frames.

    Args:
        pcm: s16le mono PCM bytes.
        sample_rate_hz: PCM sample rate.
        frame_ms: Frame duration in milliseconds.

    Returns:
        Tuple of levels in ``0..15``. Empty PCM yields an empty envelope.
    """
    logger.info(
        "_quantized_rms_envelope called pcm_bytes=%s sample_rate=%s frame_ms=%s",
        len(pcm),
        sample_rate_hz,
        frame_ms,
    )
    frame_samples = max(int(sample_rate_hz * frame_ms / 1000), 1)
    frame_bytes = frame_samples * 2
    if frame_bytes > len(pcm):
        samples = memoryview(pcm).cast("h")
        if not samples:
            return ()
        rms = math.sqrt(sum(int(sample) * int(sample) for sample in samples) / len(samples))
        return (_quantize_rms(rms),)

    levels: list[int] = []
    for offset in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        chunk = memoryview(pcm[offset : offset + frame_bytes]).cast("h")
        mean_square = sum(int(sample) * int(sample) for sample in chunk) / len(chunk)
        levels.append(_quantize_rms(math.sqrt(mean_square)))
    return tuple(levels)


def _quantize_rms(rms: float) -> int:
    """Map a linear RMS amplitude into a stable 0-15 bucket.

    Args:
        rms: Root-mean-square of signed 16-bit samples.

    Returns:
        Quantized level in ``0..15``.
    """
    # log1p compresses dynamic range so quiet/loud speech still compares.
    scaled = math.log1p(rms) / math.log1p(32768.0)
    return max(0, min(15, int(scaled * 16)))


def _aligned_mean_abs_distance(
    left: tuple[int, ...] | list[int],
    right: tuple[int, ...] | list[int],
    shift: int,
) -> float | None:
    """Mean absolute distance for one relative envelope shift.

    Args:
        left: Reference envelope.
        right: Candidate envelope.
        shift: Frames to slide ``right`` relative to ``left``.

    Returns:
        Mean absolute difference on the overlapping region, or ``None`` when the
        overlap is too short to be meaningful.
    """
    left_start = max(0, shift)
    right_start = max(0, -shift)
    overlap = min(len(left) - left_start, len(right) - right_start)
    if overlap < 3:
        return None
    total = 0
    for index in range(overlap):
        total += abs(left[left_start + index] - right[right_start + index])
    return total / overlap


def stable_lock_key(*parts: str) -> int:
    """Derive a signed 63-bit advisory-lock key from stable string parts.

    Args:
        parts: Non-secret identifiers such as speaker and fingerprint prefixes.

    Returns:
        Positive signed integer suitable for ``pg_advisory_lock``.
    """
    logger.info("stable_lock_key called part_count=%s", len(parts))
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).digest()
    return struct.unpack(">q", digest[:8])[0] & 0x7FFF_FFFF_FFFF_FFFF
