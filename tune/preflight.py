"""Fail-closed WSL2/GPU preflight for isolated Gemma 4 audio training.

The command verifies the host, Python runtime, RTX/CUDA visibility, bf16
support, free VRAM, ffmpeg tools, Hugging Face authentication and gated model
access, and free disk before model weights are downloaded. It is read-only and
does not import the backend, access Postgres, or inspect participant data.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable, Sequence

from tune.config import TuneConfig, load_config

LOGGER = logging.getLogger(__name__)
BYTES_PER_GIB = 1024**3
MIB_PER_GIB = 1024
MIN_TRANSFORMERS_VERSION = (5, 10, 0)
CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class CheckResult:
    """Represent one preflight check without exposing credentials or payloads."""

    name: str
    passed: bool
    detail: str


def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run a read-only command and capture bounded text output.

    Args:
        command: Executable and arguments with no shell interpolation.

    Returns:
        Completed process containing captured standard output and error.
    """
    LOGGER.info("run_command called executable=%s arg_count=%d", command[0], len(command) - 1)
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def check_wsl() -> CheckResult:
    """Verify execution inside an Ubuntu WSL2 Linux kernel."""
    LOGGER.info("check_wsl called platform=%s", platform.system())
    release = platform.release().lower()
    distro = os.getenv("WSL_DISTRO_NAME", "")
    passed = platform.system() == "Linux" and "microsoft-standard-wsl2" in release
    detail = f"distro={distro or 'unknown'} kernel={platform.release()}"
    return CheckResult("wsl2", passed, detail)


def check_python() -> CheckResult:
    """Verify the isolated interpreter is Python 3.12."""
    LOGGER.info("check_python called version=%s", platform.python_version())
    passed = sys.version_info[:2] == (3, 12)
    return CheckResult("python", passed, f"version={platform.python_version()}")


def check_gpu(config: TuneConfig, runner: CommandRunner = run_command) -> CheckResult:
    """Verify NVIDIA visibility and the configured minimum free VRAM.

    Args:
        config: Central training and preflight configuration.
        runner: Injectable subprocess runner used by dependency-light tests.

    Returns:
        GPU check with model and free-memory metadata.
    """
    LOGGER.info("check_gpu called min_free_vram_gib=%s", config.min_free_vram_gib)
    result = runner(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.free,driver_version,compute_cap",
            "--format=csv,noheader,nounits",
        ]
    )
    if result.returncode != 0:
        return CheckResult("gpu", False, "nvidia-smi query failed")
    fields = [field.strip() for field in result.stdout.strip().split(",")]
    if len(fields) != 4:
        return CheckResult("gpu", False, "unexpected nvidia-smi output")
    model, free_mib_text, driver, compute_capability = fields
    try:
        free_gib = float(free_mib_text) / MIB_PER_GIB
    except ValueError:
        return CheckResult("gpu", False, "invalid free-VRAM value")
    passed = "RTX 5090" in model and free_gib >= config.min_free_vram_gib
    detail = (
        f"model={model} free_vram_gib={free_gib:.1f} "
        f"driver={driver} compute_capability={compute_capability}"
    )
    return CheckResult("gpu", passed, detail)


def check_torch() -> CheckResult:
    """Verify CUDA Torch and bf16 support from the isolated training environment."""
    LOGGER.info("check_torch called")
    try:
        import torch
    except (ImportError, OSError) as exc:
        return CheckResult("torch", False, f"import failed: {type(exc).__name__}")
    cuda_available = torch.cuda.is_available()
    bf16_supported = cuda_available and torch.cuda.is_bf16_supported()
    cuda_version = tuple(
        int(part) for part in str(torch.version.cuda or "0.0").split(".")[:2]
    )
    capability = torch.cuda.get_device_capability() if cuda_available else (0, 0)
    detail = (
        f"version={torch.__version__} cuda={cuda_available} "
        f"bf16={bf16_supported} cuda_runtime={torch.version.cuda} capability={capability}"
    )
    passed = cuda_available and bf16_supported and cuda_version >= (12, 8) and capability == (12, 0)
    return CheckResult("torch", bool(passed), detail)


def check_training_packages() -> CheckResult:
    """Verify the audio-correct Unsloth and Transformers package floor."""
    LOGGER.info("check_training_packages called")
    package_names = ("unsloth", "unsloth-zoo", "transformers", "trl")
    try:
        versions = {name: version(name) for name in package_names}
    except PackageNotFoundError as exc:
        return CheckResult("training_packages", False, f"missing={exc.name}")
    transformers_parts = tuple(
        int(part) for part in versions["transformers"].split(".")[:3]
    )
    passed = transformers_parts >= MIN_TRANSFORMERS_VERSION
    detail = " ".join(f"{name}={value}" for name, value in versions.items())
    return CheckResult("training_packages", passed, detail)


def check_audio_tools() -> CheckResult:
    """Verify ffmpeg and ffprobe are discoverable on PATH."""
    LOGGER.info("check_audio_tools called")
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    passed = ffmpeg is not None and ffprobe is not None
    return CheckResult("audio_tools", passed, f"ffmpeg={bool(ffmpeg)} ffprobe={bool(ffprobe)}")


def check_hugging_face_auth(runner: CommandRunner = run_command) -> CheckResult:
    """Verify Hugging Face CLI authentication without printing a token.

    Args:
        runner: Injectable subprocess runner used by dependency-light tests.

    Returns:
        Authentication result containing only the account response.
    """
    LOGGER.info("check_hugging_face_auth called")
    result = runner(["hf", "auth", "whoami"])
    detail = result.stdout.strip() if result.returncode == 0 else "not authenticated"
    return CheckResult("hugging_face_auth", result.returncode == 0, detail[:160])


def check_model_access(
    config: TuneConfig,
    runner: CommandRunner = run_command,
) -> CheckResult:
    """Verify gated model metadata access without downloading model weights.

    Args:
        config: Central model configuration.
        runner: Injectable subprocess runner used by dependency-light tests.

    Returns:
        Model-access result for a config-only dry run.
    """
    LOGGER.info("check_model_access called model_id=%s", config.model_id)
    result = runner(["hf", "download", config.model_id, "config.json", "--dry-run"])
    passed = result.returncode == 0
    detail = f"model={config.model_id} metadata_access={passed}"
    return CheckResult("model_access", passed, detail)


def check_disk(config: TuneConfig, cache_dir: Path) -> CheckResult:
    """Verify sufficient free space on the model-cache filesystem.

    Args:
        config: Central training and preflight configuration.
        cache_dir: Intended Hugging Face cache directory.

    Returns:
        Disk check with free-space metadata.
    """
    LOGGER.info(
        "check_disk called cache_dir=%s min_free_disk_gib=%s",
        cache_dir,
        config.min_free_disk_gib,
    )
    existing = cache_dir
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    free_gib = shutil.disk_usage(existing).free / BYTES_PER_GIB
    passed = free_gib >= config.min_free_disk_gib
    return CheckResult("disk", passed, f"path={existing} free_gib={free_gib:.1f}")


def run_preflight(
    config: TuneConfig,
    cache_dir: Path,
    *,
    runner: CommandRunner = run_command,
) -> list[CheckResult]:
    """Run every environment check in a deterministic order.

    Args:
        config: Central training and preflight configuration.
        cache_dir: Intended model-cache directory.
        runner: Injectable subprocess runner used by dependency-light tests.

    Returns:
        Ordered preflight results; callers decide rendering and exit behavior.
    """
    LOGGER.info("run_preflight called model_id=%s cache_dir=%s", config.model_id, cache_dir)
    return [
        check_wsl(),
        check_python(),
        check_gpu(config, runner),
        check_torch(),
        check_training_packages(),
        check_audio_tools(),
        check_hugging_face_auth(runner),
        check_model_access(config, runner),
        check_disk(config, cache_dir),
    ]


def build_parser() -> argparse.ArgumentParser:
    """Create the environment-preflight command-line parser."""
    LOGGER.info("build_parser called")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / ".cache" / "huggingface",
        help="Model cache location whose filesystem capacity is checked.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable results.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run preflight and return non-zero unless every required check passes."""
    LOGGER.info("main called argv_provided=%s", argv is not None)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    results = run_preflight(load_config(), args.cache_dir)
    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        for result in results:
            marker = "PASS" if result.passed else "FAIL"
            print(f"[{marker}] {result.name}: {result.detail}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
