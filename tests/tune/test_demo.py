"""Dry-run rehearsal coverage for the isolated hybrid Gemma stage sequence."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from tune.config import load_config
from tune.demo import main
from tune.manifest import (
    build_artifact_manifest,
    build_dataset_manifest,
    write_manifest,
)

LOGGER = logging.getLogger(__name__)


def write_row(path: Path, utterance_id: str) -> None:
    """Write one synthetic prepared audio row for demo rehearsal.

    Args:
        path: Prepared JSONL destination.
        utterance_id: Stable synthetic row identifier.

    Side effects:
        Creates one file below the pytest temporary directory.
    """
    LOGGER.info("write_row called path_name=%s utterance_id=%s", path.name, utterance_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "utterance_id": utterance_id,
        "native_lang_tag": "as-IN",
        "target": "water pot",
        "input_mode": "audio",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "audio", "audio": "/tmp/fixture.flac"},
                    {"type": "text", "text": "Translate this speech."},
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "water pot"}]},
        ],
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def test_demo_dry_run_validates_manifests_without_creating_live_output(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Rehearse all stage commands without importing models or writing a run."""
    LOGGER.info(
        "test_demo_dry_run_validates_manifests_without_creating_live_output called temp_name=%s",
        tmp_path.name,
    )
    prepared = tmp_path / "prepared"
    train = prepared / "train.jsonl"
    holdout = prepared / "holdout.jsonl"
    write_row(train, "train-1")
    write_row(holdout, "holdout-1")
    source = tmp_path / "corpus" / "shard.jsonl"
    source.parent.mkdir()
    source.write_text("{}\n", encoding="utf-8")
    config = load_config()
    dataset = build_dataset_manifest([source], train, holdout, config)
    dataset_path = prepared / "dataset_manifest.json"
    write_manifest(dataset_path, dataset)
    adapter = tmp_path / "full-run" / "adapter"
    adapter.mkdir(parents=True)
    (adapter / "adapter_model.safetensors").write_bytes(b"verified-adapter")
    metrics = {
        "status": "completed",
        "profile": "full",
        "duration_seconds": 30.0,
        "peak_vram_gib": 12.0,
        "max_steps": None,
    }
    artifact = build_artifact_manifest(dataset, dataset_path, metrics, adapter, config)
    artifact_path = adapter.parent / "artifact_manifest.json"
    write_manifest(artifact_path, artifact)
    live_output = tmp_path / "live-run"

    assert (
        main(
            [
                "--prepared",
                str(prepared),
                "--live-run-output",
                str(live_output),
                "--full-adapter",
                str(adapter),
                "--dry-run",
            ]
        )
        == 0
    )
    assert not live_output.exists()
    assert "REHEARSAL" in capsys.readouterr().out
