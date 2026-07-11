"""Safe audio conversion and fingerprint delegation for the gauntlet.

The gauntlet invokes ffmpeg without a shell to normalize untrusted browser
uploads into contract-required 16 kHz mono FLAC. Acoustic de-duplication lives
in ``worker.fingerprint``; this module keeps a thin sync-compatible wrapper for
call sites that only need the stored ``dedup_hash`` string. Logs contain only
paths, return codes, byte counts, and hashes—never inline audio.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from worker.config import GauntletLimits
from worker.fingerprint import AcousticFingerprint, compute_acoustic_fingerprint

logger = logging.getLogger(__name__)


async def transcode_to_flac(
    source: Path, destination: Path, limits: GauntletLimits
) -> None:
    """Convert a browser recording into a 16 kHz mono FLAC archive.

    Args:
        source: Existing raw browser-audio file.
        destination: Destination FLAC file, created atomically by ffmpeg.
        limits: Centralized sample-rate, channel-count, and timeout settings.

    Raises:
        FileNotFoundError: If the source recording does not exist.
        RuntimeError: If ffmpeg fails or exceeds its configured timeout.
    """
    logger.info(
        "transcode_to_flac called source=%s source_bytes=%s destination=%s sample_rate=%s channels=%s",
        source,
        source.stat().st_size if source.exists() else None,
        destination,
        limits.sample_rate_hz,
        limits.channels,
    )
    if not source.is_file():
        raise FileNotFoundError(f"raw audio file missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-ar",
        str(limits.sample_rate_hz),
        "-ac",
        str(limits.channels),
        "-c:a",
        "flac",
        str(destination),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=limits.ffmpeg_timeout_seconds)
    except TimeoutError as error:
        process.kill()
        await process.wait()
        raise RuntimeError("ffmpeg transcode timed out") from error
    if process.returncode != 0:
        reason = stderr.decode("utf-8", errors="replace").splitlines()[-1:] or ["unknown ffmpeg error"]
        raise RuntimeError(f"ffmpeg transcode failed: {reason[0][:240]}")
    logger.info("transcode_to_flac completed destination=%s bytes=%s", destination, destination.stat().st_size)


async def audio_fingerprint(flac_path: Path, limits: GauntletLimits) -> AcousticFingerprint:
    """Compute the deterministic acoustic fingerprint for a clean FLAC file.

    Args:
        flac_path: Existing clean FLAC recording.
        limits: Decode and envelope configuration.

    Returns:
        Acoustic fingerprint whose ``dedup_hash`` is persisted on the turn.
    """
    logger.info(
        "audio_fingerprint called flac_path=%s bytes=%s",
        flac_path,
        flac_path.stat().st_size if flac_path.exists() else None,
    )
    return await compute_acoustic_fingerprint(flac_path, limits)
