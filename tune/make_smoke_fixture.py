"""Create valid temporary FLAC fixtures for a one-step GPU smoke tune.

The fixture is synthetic and intentionally unsuitable for model-quality claims:
each eligible golden record points to a short generated tone rather than human
speech. The caller must choose an empty output directory outside ``data/``.
``--dry-run`` validates intent without writing any files.
"""

from __future__ import annotations

import argparse
import logging
import math
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from tune.config import load_config
from tune.make_dummy import generate_dummy, validate_output

LOGGER = logging.getLogger(__name__)
SAMPLE_RATE_HZ = 16_000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2
FIXTURE_DURATION_SECONDS = 1.0
BASE_FREQUENCY_HZ = 220
FREQUENCY_STEP_HZ = 20
AMPLITUDE = 0.2


def write_tone_wav(path: Path, frequency_hz: int) -> None:
    """Write one short mono PCM tone used only for decoder smoke coverage.

    Args:
        path: Caller-owned temporary WAV destination.
        frequency_hz: Tone frequency in hertz.

    Side effects:
        Creates one temporary WAV file outside the runtime corpus.
    """
    LOGGER.info(
        "write_tone_wav called path_name=%s frequency_hz=%d duration_seconds=%s",
        path.name,
        frequency_hz,
        FIXTURE_DURATION_SECONDS,
    )
    sample_count = int(SAMPLE_RATE_HZ * FIXTURE_DURATION_SECONDS)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(CHANNELS)
        audio.setsampwidth(SAMPLE_WIDTH_BYTES)
        audio.setframerate(SAMPLE_RATE_HZ)
        for index in range(sample_count):
            value = int(
                AMPLITUDE
                * ((2 ** (SAMPLE_WIDTH_BYTES * 8 - 1)) - 1)
                * math.sin(2 * math.pi * frequency_hz * index / SAMPLE_RATE_HZ)
            )
            audio.writeframesraw(struct.pack("<h", value))


def transcode_fixture(source: Path, destination: Path) -> None:
    """Convert a temporary WAV fixture to contract-compatible mono FLAC.

    Args:
        source: Existing generated WAV path.
        destination: FLAC path under the caller-owned fixture directory.

    Raises:
        RuntimeError: If ffmpeg is unavailable or conversion fails.

    Side effects:
        Runs ffmpeg and replaces the dummy FLAC stub at ``destination``.
    """
    LOGGER.info(
        "transcode_fixture called source_name=%s destination_name=%s",
        source.name,
        destination.name,
    )
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required to create valid smoke audio")
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-ar",
            str(SAMPLE_RATE_HZ),
            "-ac",
            str(CHANNELS),
            "-c:a",
            "flac",
            str(destination),
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("ffmpeg failed to create a valid smoke FLAC fixture")


def generate_smoke_fixture(output: Path, rows: int) -> int:
    """Generate canonical records and replace their FLAC stubs with valid audio.

    Args:
        output: New or empty caller-owned directory outside the runtime corpus.
        rows: Number of deterministic synthetic examples to create.

    Returns:
        Number of valid FLAC files created.

    Side effects:
        Writes a synthetic corpus, transcript sidecar, and valid FLAC fixtures.
    """
    LOGGER.info("generate_smoke_fixture called output_name=%s rows=%d", output.name, rows)
    generate_dummy(output, rows)
    destinations = sorted((output / "audio").glob("*.flac"))
    with tempfile.TemporaryDirectory(prefix="gemma-audio-smoke-") as temporary:
        temp_dir = Path(temporary)
        for index, destination in enumerate(destinations):
            wav_path = temp_dir / f"tone-{index:03d}.wav"
            write_tone_wav(wav_path, BASE_FREQUENCY_HZ + index * FREQUENCY_STEP_HZ)
            transcode_fixture(wav_path, destination)
    return len(destinations)


def build_parser() -> argparse.ArgumentParser:
    """Create the valid-audio smoke-fixture command-line parser."""
    LOGGER.info("build_parser called")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=int)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate or create isolated valid-audio smoke fixtures."""
    LOGGER.info("main called argv_provided=%s", argv is not None)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    config = load_config()
    rows = args.rows if args.rows is not None else config.smoke_fixture_rows
    if rows < 2:
        raise SystemExit("--rows must be at least 2 so preparation creates a holdout")
    validate_output(args.output, args.dry_run)
    if args.dry_run:
        print(f"DRY RUN OK: would create valid synthetic FLAC rows={rows} at {args.output}")
        return 0
    created = generate_smoke_fixture(args.output, rows)
    print(f"Created valid synthetic FLAC smoke fixture rows={created} at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
