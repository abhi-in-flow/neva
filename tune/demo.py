"""Run the honest hybrid Gemma audio stage sequence without app dependencies.

The sequence verifies the environment and frozen corpus, attempts a short live
optimizer run, switches explicitly to a separately pre-completed compatible
adapter, shows deterministic held-out comparisons, and optionally compares one
temporary microphone recording. A failed live run or microphone capture is
reported and does not stop verified-adapter inference.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from tune.compare import compare_models, print_comparison
from tune.config import load_config
from tune.live import build_live_row as build_temporary_live_row
from tune.live import normalize_live_audio
from tune.manifest import (
    load_manifest,
    validate_artifact_compatibility,
    validate_dataset_files,
)

LOGGER = logging.getLogger(__name__)
STAGE_PREFIX = "=== GEMMA AUDIO DEMO ==="


def stage(message: str) -> None:
    """Print one judge-readable stage transition.

    Args:
        message: Non-sensitive progress statement.
    """
    LOGGER.info("stage called message_length=%d", len(message))
    print(f"\n{STAGE_PREFIX} {message}", flush=True)


def run_command(command: Sequence[str], *, allow_failure: bool = False) -> bool:
    """Run one reproducible stage command and expose failure honestly.

    Args:
        command: Executable and arguments with no shell interpolation.
        allow_failure: Return ``False`` instead of raising on non-zero exit.

    Returns:
        ``True`` when the command completed successfully.

    Raises:
        RuntimeError: If a required command fails.
    """
    LOGGER.info(
        "run_command called executable=%s arg_count=%d allow_failure=%s",
        command[0],
        len(command) - 1,
        allow_failure,
    )
    result = subprocess.run(list(command), check=False)
    if result.returncode == 0:
        return True
    if allow_failure:
        print(f"{STAGE_PREFIX} optional step failed with exit={result.returncode}; continuing")
        return False
    raise RuntimeError(f"required demo command failed with exit={result.returncode}")


def normalize_demo_audio(source: Path, destination: Path) -> None:
    """Normalize temporary microphone audio to Gemma's 16 kHz mono FLAC input.

    Args:
        source: Existing operator-selected recording outside the corpus.
        destination: Temporary normalized FLAC path.

    Raises:
        RuntimeError: If ffmpeg is missing or normalization fails.

    Side effects:
        Runs ffmpeg and writes only to the temporary destination.
    """
    LOGGER.info(
        "normalize_demo_audio called source_name=%s destination_name=%s",
        source.name,
        destination.name,
    )
    normalize_live_audio(source, destination, load_config())


def build_live_row(audio_path: Path, native_language: str) -> dict[str, object]:
    """Build one temporary inference-only conversation outside the corpus.

    Args:
        audio_path: Normalized temporary 16 kHz mono FLAC.
        native_language: Operator-declared source language or dialect.

    Returns:
        Prepared-shape row accepted by the shared comparison implementation.
    """
    LOGGER.info(
        "build_live_row called audio_name=%s native_language_length=%d",
        audio_path.name,
        len(native_language),
    )
    return build_temporary_live_row(audio_path, native_language, load_config())


def render_frozen_summary(dataset_manifest: dict[str, object]) -> None:
    """Print only aggregate frozen-corpus facts suitable for the stage.

    Args:
        dataset_manifest: Loaded private dataset manifest.
    """
    LOGGER.info("render_frozen_summary called status=%s", dataset_manifest.get("status"))
    counts = dataset_manifest["sample_counts"]
    languages = dataset_manifest["language_counts"]
    print(
        f"Frozen samples: {counts['total']} "
        f"(train={counts['train']}, holdout={counts['holdout']})"
    )
    print("Languages: " + ", ".join(f"{name}={count}" for name, count in languages.items()))
    print(f"Corpus hash: {str(dataset_manifest['source_corpus_sha256'])[:16]}…")


def build_parser() -> argparse.ArgumentParser:
    """Create the hybrid stage-orchestrator command-line parser."""
    LOGGER.info("build_parser called")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--live-run-output", type=Path, required=True)
    parser.add_argument("--full-adapter", type=Path, required=True)
    parser.add_argument("--full-artifact-manifest", type=Path)
    parser.add_argument("--live-audio", type=Path)
    parser.add_argument("--fallback-audio", type=Path)
    parser.add_argument("--native-language")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run or safely rehearse the complete hybrid Gemma audio stage sequence."""
    LOGGER.info("main called argv_provided=%s", argv is not None)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    config = load_config()
    train_path = args.prepared / "train.jsonl"
    holdout_path = args.prepared / "holdout.jsonl"
    dataset_manifest_path = args.prepared / "dataset_manifest.json"
    dataset_manifest = load_manifest(dataset_manifest_path)
    validate_dataset_files(dataset_manifest, train_path, holdout_path)
    artifact_manifest_path = (
        args.full_artifact_manifest
        or args.full_adapter.parent / "artifact_manifest.json"
    )
    artifact_manifest = load_manifest(artifact_manifest_path)
    validate_artifact_compatibility(dataset_manifest, artifact_manifest, args.full_adapter)

    preflight_command = [
        sys.executable,
        "-m",
        "tune.preflight",
    ]
    live_train_command = [
        sys.executable,
        "-m",
        "tune.train",
        "--train",
        str(train_path),
        "--dataset-manifest",
        str(dataset_manifest_path),
        "--output",
        str(args.live_run_output),
        "--max-steps",
        str(config.smoke_max_steps),
    ]
    compare_command = [
        sys.executable,
        "-m",
        "tune.compare",
        "--holdout",
        str(holdout_path),
        "--dataset-manifest",
        str(dataset_manifest_path),
        "--adapter",
        str(args.full_adapter),
        "--artifact-manifest",
        str(artifact_manifest_path),
    ]
    if args.dry_run:
        stage("REHEARSAL — no model or GPU work")
        render_frozen_summary(dataset_manifest)
        for command in (preflight_command, live_train_command, compare_command):
            print("DRY RUN:", " ".join(command))
        return 0

    stage("1/5 PREFLIGHT")
    run_command(preflight_command)
    stage("2/5 FROZEN CORPUS")
    render_frozen_summary(dataset_manifest)
    stage("3/5 SHORT LIVE QLORA — separate smoke adapter")
    run_command(live_train_command, allow_failure=True)
    stage("4/5 VERIFIED FULL ADAPTER — deterministic held-out comparisons")
    run_command(compare_command)
    stage("5/5 TEMPORARY LIVE MICROPHONE COMPARISON")
    selected_audio = args.live_audio
    if selected_audio is None or not selected_audio.is_file():
        selected_audio = args.fallback_audio
        print(f"{STAGE_PREFIX} live capture unavailable; using validated fallback recording")
    if selected_audio is None or not selected_audio.is_file():
        print(f"{STAGE_PREFIX} no live or fallback recording available; skipping final comparison")
        return 0
    if not args.native_language:
        raise SystemExit("--native-language is required when live or fallback audio is used")
    try:
        with tempfile.TemporaryDirectory(prefix="gemma-live-demo-") as temporary:
            normalized = Path(temporary) / "live.flac"
            normalize_demo_audio(selected_audio, normalized)
            result = compare_models(
                [build_live_row(normalized, args.native_language)],
                args.full_adapter,
                config,
            )
            print_comparison(result)
    except Exception as exc:
        print(f"{STAGE_PREFIX} live comparison failed honestly: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
