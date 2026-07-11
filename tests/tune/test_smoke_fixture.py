"""Tests for temporary valid-audio Gemma GPU smoke fixtures.

The tests write only below pytest's temporary directory and require ffmpeg,
which is already a runtime dependency of the cleaning gauntlet. No ML package,
model weight, database, frontend, or real corpus is accessed.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from tune.make_smoke_fixture import generate_smoke_fixture, main
from tune.prepare import validate_and_prepare
from tune.config import load_config

LOGGER = logging.getLogger(__name__)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_smoke_fixture_contains_decodable_16khz_mono_flac(tmp_path: Path) -> None:
    """Generate valid temporary audio and prove ffprobe can decode its format."""
    LOGGER.info(
        "test_smoke_fixture_contains_decodable_16khz_mono_flac called temp_name=%s",
        tmp_path.name,
    )
    fixture = tmp_path / "fixture"
    assert generate_smoke_fixture(fixture, 10) == 10
    first_audio = next((fixture / "audio").glob("*.flac"))

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels",
            "-of",
            "json",
            str(first_audio),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    stream = json.loads(result.stdout)["streams"][0]
    train, holdout, source_count = validate_and_prepare(
        fixture / "corpus",
        fixture,
        "audio",
        None,
        load_config(),
    )

    assert stream == {"codec_name": "flac", "sample_rate": "16000", "channels": 1}
    assert source_count == 10
    assert len(train) == 8
    assert len(holdout) == 2


def test_smoke_fixture_dry_run_does_not_write(tmp_path: Path) -> None:
    """Validate fixture intent without creating the requested output directory."""
    LOGGER.info(
        "test_smoke_fixture_dry_run_does_not_write called temp_name=%s",
        tmp_path.name,
    )
    output = tmp_path / "not-created"

    assert main(["--output", str(output), "--dry-run"]) == 0
    assert not output.exists()
