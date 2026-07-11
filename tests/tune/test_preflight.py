"""Dependency-light tests for isolated Gemma audio environment preflight.

The checks use injected command results and temporary filesystems. They never
import Torch, contact Hugging Face, download models, or inspect the real corpus.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Sequence
from unittest.mock import patch

from tune.config import load_config
from tune.preflight import check_disk, check_gpu, check_model_access

LOGGER = logging.getLogger(__name__)


def completed(command: Sequence[str], returncode: int, stdout: str = "") -> subprocess.CompletedProcess[str]:
    """Build a deterministic completed-process fixture.

    Args:
        command: Simulated executable and arguments.
        returncode: Simulated process exit status.
        stdout: Simulated standard output.

    Returns:
        Completed process suitable for injected preflight runners.
    """
    LOGGER.info(
        "completed called executable=%s arg_count=%d returncode=%d",
        command[0],
        len(command) - 1,
        returncode,
    )
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")


def test_config_separates_smoke_controls_and_uses_instruction_model() -> None:
    """Keep smoke limits explicit and pin the intended instruction profile."""
    LOGGER.info("test_config_separates_smoke_controls_and_uses_instruction_model called")
    config = load_config()

    assert config.model_id == "unsloth/gemma-4-E4B-it"
    assert config.smoke_max_steps == 1
    assert config.epochs == 3
    assert config.min_free_vram_gib == 17
    assert config.attention_implementation == "sdpa"


def test_gpu_check_requires_5090_and_configured_free_vram() -> None:
    """Accept the target GPU only when its free VRAM clears the safety floor."""
    LOGGER.info("test_gpu_check_requires_5090_and_configured_free_vram called")
    config = load_config()

    passing = check_gpu(
        config,
        lambda command: completed(
            command,
            0,
            "NVIDIA GeForce RTX 5090 Laptop GPU, 23000, 591.91, 12.0\n",
        ),
    )
    failing = check_gpu(
        config,
        lambda command: completed(
            command,
            0,
            "NVIDIA GeForce RTX 5090 Laptop GPU, 12000, 591.91, 12.0\n",
        ),
    )

    assert passing.passed is True
    assert failing.passed is False
    assert "free_vram_gib=" in passing.detail


def test_model_access_uses_metadata_only_dry_run() -> None:
    """Check gated access through config metadata without downloading weights."""
    LOGGER.info("test_model_access_uses_metadata_only_dry_run called")
    config = load_config()
    commands: list[list[str]] = []

    def runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        """Capture the model-access command and return success."""
        commands.append(list(command))
        return completed(command, 0, "dry-run")

    result = check_model_access(config, runner)

    assert result.passed is True
    assert commands == [["hf", "download", config.model_id, "config.json", "--dry-run"]]


def test_disk_check_uses_existing_parent_for_new_cache(tmp_path: Path) -> None:
    """Permit a not-yet-created cache directory on a sufficiently large volume."""
    LOGGER.info(
        "test_disk_check_uses_existing_parent_for_new_cache called temp_name=%s",
        tmp_path.name,
    )
    with patch("tune.preflight.shutil.disk_usage") as disk_usage:
        disk_usage.return_value.free = 100 * 1024**3
        result = check_disk(load_config(), tmp_path / "future" / "huggingface")

    assert result.passed is True
    assert str(tmp_path) in result.detail
