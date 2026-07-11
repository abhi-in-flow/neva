"""Tests for frozen dataset and adapter compatibility manifests.

All files are synthetic and live below pytest temporary directories. The tests
prove exact corpus/adapter binding and fail-closed mismatch behavior without
loading model dependencies.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from tune.config import load_config
from tune.manifest import (
    build_artifact_manifest,
    build_dataset_manifest,
    load_manifest,
    validate_artifact_compatibility,
    validate_dataset_files,
    write_manifest,
)

LOGGER = logging.getLogger(__name__)


def write_prepared(path: Path, utterance_id: str, language: str) -> None:
    """Write one minimal prepared row for manifest identity tests.

    Args:
        path: Prepared JSONL destination.
        utterance_id: Stable synthetic identifier.
        language: Synthetic source-language tag.

    Side effects:
        Creates one JSONL file below the caller-owned temporary directory.
    """
    LOGGER.info(
        "write_prepared called path_name=%s utterance_id=%s language=%s",
        path.name,
        utterance_id,
        language,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "utterance_id": utterance_id,
        "native_lang_tag": language,
        "target": "water pot",
        "input_mode": "audio",
        "messages": [],
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def test_artifact_manifest_accepts_only_matching_dataset_and_adapter(tmp_path: Path) -> None:
    """Bind an adapter to a frozen split and reject later adapter mutation."""
    LOGGER.info(
        "test_artifact_manifest_accepts_only_matching_dataset_and_adapter called temp_name=%s",
        tmp_path.name,
    )
    source = tmp_path / "corpus" / "shard_0001.jsonl"
    source.parent.mkdir()
    source.write_text('{"training_eligible": true}\n', encoding="utf-8")
    train = tmp_path / "prepared" / "train.jsonl"
    holdout = tmp_path / "prepared" / "holdout.jsonl"
    write_prepared(train, "train-1", "as-IN")
    write_prepared(holdout, "holdout-1", "bn-IN")
    config = load_config()
    dataset = build_dataset_manifest([source], train, holdout, config)
    dataset_path = tmp_path / "prepared" / "dataset_manifest.json"
    write_manifest(dataset_path, dataset)
    adapter = tmp_path / "run" / "adapter"
    adapter.mkdir(parents=True)
    (adapter / "adapter_model.safetensors").write_bytes(b"synthetic-adapter")
    metrics = {
        "status": "completed",
        "profile": "smoke",
        "duration_seconds": 1.0,
        "peak_vram_gib": 2.0,
        "max_steps": 1,
    }
    artifact = build_artifact_manifest(dataset, dataset_path, metrics, adapter, config)

    validate_dataset_files(load_manifest(dataset_path), train, holdout)
    validate_artifact_compatibility(dataset, artifact, adapter)
    (adapter / "adapter_model.safetensors").write_bytes(b"mutated-adapter")
    with pytest.raises(ValueError, match="adapter directory"):
        validate_artifact_compatibility(dataset, artifact, adapter)


def test_dataset_manifest_rejects_modified_holdout(tmp_path: Path) -> None:
    """Detect any post-freeze change to deterministic evaluation data."""
    LOGGER.info(
        "test_dataset_manifest_rejects_modified_holdout called temp_name=%s",
        tmp_path.name,
    )
    source = tmp_path / "shard.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    train = tmp_path / "train.jsonl"
    holdout = tmp_path / "holdout.jsonl"
    write_prepared(train, "train-1", "as-IN")
    write_prepared(holdout, "holdout-1", "as-IN")
    manifest = build_dataset_manifest([source], train, holdout, load_config())
    holdout.write_text(holdout.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="holdout JSONL"):
        validate_dataset_files(manifest, train, holdout)
