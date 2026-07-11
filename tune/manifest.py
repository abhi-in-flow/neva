"""Freeze prepared-corpus identity and enforce adapter compatibility.

Dataset manifests bind source shards, deterministic train/holdout files, model
profile, split seed, and language/sample counts. Artifact manifests then bind a
completed adapter and private training metrics to that frozen dataset. The
demo refuses mismatched manifests rather than presenting unrelated outputs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from tune.config import TuneConfig
from tune.prepare import read_jsonl

LOGGER = logging.getLogger(__name__)
MANIFEST_SCHEMA_VERSION = 1


def sha256_file(path: Path) -> str:
    """Hash one file without logging or loading its participant content.

    Args:
        path: Existing file to hash.

    Returns:
        Lowercase SHA-256 hexadecimal digest.
    """
    LOGGER.info("sha256_file called path_name=%s", path.name)
    if not path.is_file():
        raise ValueError(f"manifest input file does not exist: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_files(paths: Iterable[Path]) -> str:
    """Hash an ordered collection of files with unambiguous name boundaries.

    Args:
        paths: Files whose names and bytes define a frozen corpus.

    Returns:
        Combined SHA-256 hexadecimal digest.
    """
    ordered = sorted(path.resolve() for path in paths)
    LOGGER.info("sha256_files called file_count=%d", len(ordered))
    if not ordered:
        raise ValueError("at least one file is required for a corpus hash")
    digest = hashlib.sha256()
    for path in ordered:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
        digest.update(b"\0")
    return digest.hexdigest()


def sha256_directory(path: Path) -> str:
    """Hash every regular adapter file by relative POSIX path and content.

    Args:
        path: Completed adapter directory.

    Returns:
        Combined SHA-256 hexadecimal digest.
    """
    LOGGER.info("sha256_directory called path_name=%s", path.name)
    if not path.is_dir():
        raise ValueError(f"adapter directory does not exist: {path}")
    files = sorted(item for item in path.rglob("*") if item.is_file())
    if not files:
        raise ValueError("adapter directory is empty")
    digest = hashlib.sha256()
    for item in files:
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(item)))
        digest.update(b"\0")
    return digest.hexdigest()


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write one private manifest beside its owned artifacts.

    Args:
        path: Manifest destination selected by the harness.
        payload: Non-secret manifest object.

    Side effects:
        Creates or replaces ``path`` and restricts it to the current user.
    """
    LOGGER.info("write_manifest called path_name=%s keys=%s", path.name, sorted(payload))
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(serialized)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)
    try:
        path.chmod(0o600)
    except OSError:
        LOGGER.warning("write_manifest could not set restrictive permissions")


def load_manifest(path: Path) -> dict[str, Any]:
    """Load and minimally validate a harness manifest.

    Args:
        path: Existing dataset or artifact manifest.

    Returns:
        Parsed manifest object.
    """
    LOGGER.info("load_manifest called path_name=%s", path.name)
    if not path.is_file():
        raise ValueError(f"manifest does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported or invalid manifest")
    return payload


def build_dataset_manifest(
    source_shards: Iterable[Path],
    train_path: Path,
    holdout_path: Path,
    config: TuneConfig,
) -> dict[str, Any]:
    """Build the immutable identity of one prepared corpus split.

    Args:
        source_shards: Canonical input JSONL shards, read-only.
        train_path: Prepared training JSONL.
        holdout_path: Prepared holdout JSONL.
        config: Split and model profile.

    Returns:
        Dataset manifest ready for private atomic writing.
    """
    source_shards = list(source_shards)
    LOGGER.info(
        "build_dataset_manifest called source_count=%d train_name=%s holdout_name=%s",
        len(source_shards),
        train_path.name,
        holdout_path.name,
    )
    train_rows = read_jsonl([train_path])
    holdout_rows = read_jsonl([holdout_path])
    all_rows = train_rows + holdout_rows
    languages = Counter(str(row.get("native_lang_tag")) for row in all_rows)
    modes = {row.get("input_mode") for row in all_rows}
    if len(modes) != 1:
        raise ValueError("prepared dataset manifest requires exactly one input mode")
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": "dataset",
        "status": "frozen",
        "created_at": datetime.now(UTC).isoformat(),
        "model_id": config.model_id,
        "input_mode": next(iter(modes)),
        "source_corpus_sha256": sha256_files(source_shards),
        "train_sha256": sha256_file(train_path),
        "holdout_sha256": sha256_file(holdout_path),
        "split_seed": config.split_seed,
        "holdout_fraction": config.holdout_fraction,
        "sample_counts": {
            "total": len(all_rows),
            "train": len(train_rows),
            "holdout": len(holdout_rows),
        },
        "language_counts": dict(sorted(languages.items())),
    }


def validate_dataset_files(
    manifest: dict[str, Any],
    train_path: Path,
    holdout_path: Path,
) -> None:
    """Fail if prepared files no longer match their frozen dataset manifest.

    Args:
        manifest: Loaded dataset manifest.
        train_path: Current prepared training JSONL.
        holdout_path: Current prepared holdout JSONL.
    """
    LOGGER.info(
        "validate_dataset_files called train_name=%s holdout_name=%s",
        train_path.name,
        holdout_path.name,
    )
    if manifest.get("kind") != "dataset" or manifest.get("status") != "frozen":
        raise ValueError("dataset manifest is not frozen")
    if manifest.get("train_sha256") != sha256_file(train_path):
        raise ValueError("training JSONL does not match frozen dataset manifest")
    if manifest.get("holdout_sha256") != sha256_file(holdout_path):
        raise ValueError("holdout JSONL does not match frozen dataset manifest")


def build_artifact_manifest(
    dataset_manifest: dict[str, Any],
    dataset_manifest_path: Path,
    training_metrics: dict[str, Any],
    adapter_dir: Path,
    config: TuneConfig,
) -> dict[str, Any]:
    """Bind a completed adapter to its exact frozen corpus and run settings.

    Args:
        dataset_manifest: Loaded frozen dataset identity.
        dataset_manifest_path: File whose hash is bound to the adapter.
        training_metrics: Completed private metrics returned by training.
        adapter_dir: Saved PEFT adapter and processor directory.
        config: Centralized LoRA configuration.

    Returns:
        Completed artifact manifest.
    """
    LOGGER.info(
        "build_artifact_manifest called adapter_name=%s status=%s",
        adapter_dir.name,
        training_metrics.get("status"),
    )
    if dataset_manifest.get("status") != "frozen":
        raise ValueError("artifact requires a frozen dataset manifest")
    if training_metrics.get("status") != "completed":
        raise ValueError("artifact manifest requires completed training")
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": "adapter",
        "status": "completed",
        "created_at": datetime.now(UTC).isoformat(),
        "model_id": config.model_id,
        "dataset_manifest_sha256": sha256_file(dataset_manifest_path),
        "source_corpus_sha256": dataset_manifest["source_corpus_sha256"],
        "train_sha256": dataset_manifest["train_sha256"],
        "holdout_sha256": dataset_manifest["holdout_sha256"],
        "split_seed": config.split_seed,
        "sample_counts": dataset_manifest["sample_counts"],
        "language_counts": dataset_manifest["language_counts"],
        "lora": {
            "rank": config.lora_rank,
            "alpha": config.lora_alpha,
            "dropout": config.lora_dropout,
            "target_modules": list(config.target_modules),
        },
        "training": {
            "profile": training_metrics["profile"],
            "duration_seconds": training_metrics["duration_seconds"],
            "peak_vram_gib": training_metrics["peak_vram_gib"],
            "max_steps": training_metrics["max_steps"],
        },
        "adapter_path": str(adapter_dir.resolve()),
        "adapter_sha256": sha256_directory(adapter_dir),
    }


def validate_artifact_compatibility(
    dataset_manifest: dict[str, Any],
    artifact_manifest: dict[str, Any],
    adapter_dir: Path,
) -> None:
    """Refuse base-versus-adapter use when corpus or adapter identity differs.

    Args:
        dataset_manifest: Frozen dataset selected for evaluation.
        artifact_manifest: Completed adapter manifest.
        adapter_dir: Adapter directory selected for inference.
    """
    LOGGER.info("validate_artifact_compatibility called adapter_name=%s", adapter_dir.name)
    if artifact_manifest.get("kind") != "adapter" or artifact_manifest.get("status") != "completed":
        raise ValueError("adapter manifest is incomplete")
    for field in ("model_id", "source_corpus_sha256", "train_sha256", "holdout_sha256", "split_seed"):
        if artifact_manifest.get(field) != dataset_manifest.get(field):
            raise ValueError(f"adapter is incompatible with dataset manifest: {field}")
    if artifact_manifest.get("adapter_sha256") != sha256_directory(adapter_dir):
        raise ValueError("adapter directory does not match artifact manifest")
