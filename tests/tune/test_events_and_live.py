"""Tests for safe tuning events and temporary live-row construction."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tune.compare import main as compare_main
from tune.compare import verify_inference_audio_inputs
from tune.config import load_config
from tune.events import JsonlEventWriter, write_result
from tune.live import build_live_row, probe_live_audio_duration

LOGGER = logging.getLogger(__name__)


def test_event_writer_drops_paths_and_bounds_progress_metadata(tmp_path: Path) -> None:
    """Emit one JSONL event without exposing an unrestricted absolute path."""
    LOGGER.info("test_event_writer_drops_paths_and_bounds_progress_metadata called")
    destination = tmp_path / "events.jsonl"
    writer = JsonlEventWriter(destination)

    writer.emit(
        "training",
        0.5,
        "safe progress",
        sample_count=4,
        adapter_path="/private/adapter",
    )

    event = json.loads(destination.read_text(encoding="utf-8"))
    assert event["stage"] == "training"
    assert event["sample_count"] == 4
    assert "adapter_path" not in event


def test_result_writer_replaces_json_atomically(tmp_path: Path) -> None:
    """Replace an existing structured result without leaving temporary files."""
    LOGGER.info("test_result_writer_replaces_json_atomically called")
    destination = tmp_path / "result.json"
    write_result(destination, {"status": "first"})
    write_result(destination, {"status": "completed"})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"status": "completed"}
    assert list(tmp_path.iterdir()) == [destination]


def test_live_row_is_temporary_audio_first_and_language_bounded(tmp_path: Path) -> None:
    """Build exactly one inference-only row with bounded native-language text."""
    LOGGER.info("test_live_row_is_temporary_audio_first_and_language_bounded called")
    audio = tmp_path / "live.flac"
    audio.write_bytes(b"fLaC")
    config = load_config()

    row = build_live_row(audio, "A" * (config.native_language_chars + 20), config)

    assert row["utterance_id"] == "temporary-live-demo"
    assert row["input_mode"] == "audio"
    assert len(row["native_lang_tag"]) == config.native_language_chars
    assert row["messages"][0]["content"][0]["type"] == "audio"
    assert row["messages"][0]["content"][1]["type"] == "text"
    instruction = row["messages"][0]["content"][1]["text"]
    assert "attached" in instruction
    assert "speech describe" in instruction
    assert "short English phrase" in instruction


def test_inference_requires_encoded_audio_features() -> None:
    """Reject inference batches that omit attached-audio feature tensors."""
    LOGGER.info("test_inference_requires_encoded_audio_features called")
    verify_inference_audio_inputs(
        {"input_features": object(), "input_features_mask": object(), "input_ids": object()}
    )

    with pytest.raises(RuntimeError, match="did not encode attached audio"):
        verify_inference_audio_inputs({"input_ids": object()})


def test_compare_cli_runs_one_temporary_live_row(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Exercise live CLI plumbing without loading model or audio dependencies."""
    LOGGER.info("test_compare_cli_runs_one_temporary_live_row called")
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    row = {
        "utterance_id": "holdout",
        "native_lang_tag": "as-IN",
        "target": "water pot",
        "input_mode": "audio",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "audio", "audio": "/fixture.flac"},
                    {"type": "text", "text": "Translate this speech."},
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "water pot"}]},
        ],
    }
    holdout = prepared / "holdout.jsonl"
    holdout.write_text(json.dumps(row) + "\n", encoding="utf-8")
    (prepared / "train.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    upload = tmp_path / "live.webm"
    upload.write_bytes(b"fixture")
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    result_path = tmp_path / "result.json"

    def fake_normalize(source: Path, destination: Path, config: object) -> None:
        """Create a synthetic normalized file in the CLI temporary directory."""
        LOGGER.info("fake_normalize called source_name=%s", source.name)
        destination.write_bytes(b"fLaC")

    def fake_compare(
        rows: list[dict[str, Any]],
        selected_adapter: Path,
        config: object,
        *,
        event_writer: object,
    ) -> list[dict[str, str]]:
        """Return deterministic bounded outputs for the one temporary row."""
        LOGGER.info(
            "fake_compare called row_count=%d adapter_name=%s",
            len(rows),
            selected_adapter.name,
        )
        assert len(rows) == 1
        assert rows[0]["utterance_id"] == "temporary-live-demo"
        return [
            {
                "utterance_id": "temporary-live-demo",
                "target": "(live target not known)",
                "audio_path": "/temporary/live.flac",
                "base": "base",
                "tuned": "tuned",
            }
        ]

    monkeypatch.setattr("tune.compare.load_manifest", lambda path: {"kind": path.name})
    monkeypatch.setattr("tune.compare.validate_dataset_files", lambda *args: None)
    monkeypatch.setattr("tune.compare.validate_artifact_compatibility", lambda *args: None)
    monkeypatch.setattr("tune.compare.normalize_live_audio", fake_normalize)
    monkeypatch.setattr("tune.compare.probe_live_audio_duration", lambda *args: 2.0)
    monkeypatch.setattr("tune.compare.compare_models", fake_compare)

    assert (
        compare_main(
            [
                "--holdout",
                str(holdout),
                "--adapter",
                str(adapter),
                "--live-audio",
                str(upload),
                "--native-language",
                "Assamese",
                "--result",
                str(result_path),
            ]
        )
        == 0
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["kind"] == "infer_live"
    assert result["sample_count"] == 1
    assert result["base_output"] == "base"
    assert result["tuned_output"] == "tuned"
    assert "audio_path" not in result["predictions"][0]


@pytest.mark.parametrize(
    ("duration", "accepted"),
    [(0.9, False), (1.0, True), (8.0, True), (8.1, False)],
)
def test_live_duration_uses_fixed_ffprobe_and_bounds(
    tmp_path: Path,
    monkeypatch: Any,
    duration: float,
    accepted: bool,
) -> None:
    """Accept only configured 1–8 second normalized recordings."""
    LOGGER.info(
        "test_live_duration_uses_fixed_ffprobe_and_bounds called duration=%s",
        duration,
    )
    audio = tmp_path / "live.flac"
    audio.write_bytes(b"fLaC")
    commands: list[tuple[list[str], dict[str, Any]]] = []

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Capture fixed ffprobe arguments and return a synthetic duration."""
        LOGGER.info("duration runner called executable=%s", command[0])
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=str(duration), stderr="")

    monkeypatch.setattr("tune.live.shutil.which", lambda executable: f"/usr/bin/{executable}")
    if accepted:
        assert probe_live_audio_duration(audio, load_config(), runner=runner) == duration
    else:
        with pytest.raises(ValueError, match="between 1 and 8 seconds"):
            probe_live_audio_duration(audio, load_config(), runner=runner)
    command, kwargs = commands[0]
    assert command[:7] == [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
    ]
    assert kwargs["shell"] is False
