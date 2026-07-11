"""Smoke coverage for deterministic dummy generation and corpus preparation.

These tests use only temporary directories and standard-library FLAC signature
fixtures. They verify eligibility, language-stratified splitting, audio-first
conversation structure, deterministic reruns, and explicit transcript fallback
without model downloads, GPUs, databases, or runtime corpus writes.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

import pytest

from tune.config import load_config
from tune.make_dummy import generate_dummy
from tune.prepare import resolve_audio_path, validate_and_prepare, write_jsonl

LOGGER = logging.getLogger(__name__)


def test_dummy_audio_preparation_is_deterministic_and_stratified(tmp_path: Path) -> None:
    """Prepare 100 synthetic audio rows into deterministic 80/20 language strata."""
    LOGGER.info(
        "test_dummy_audio_preparation_is_deterministic_and_stratified called temp_name=%s",
        tmp_path.name,
    )
    dummy_dir = tmp_path / "dummy"
    generate_dummy(dummy_dir, 100)
    config = load_config()

    first_train, first_holdout, source_count = validate_and_prepare(
        dummy_dir / "corpus",
        dummy_dir,
        "audio",
        None,
        config,
    )
    second_train, second_holdout, _ = validate_and_prepare(
        dummy_dir / "corpus",
        dummy_dir,
        "audio",
        None,
        config,
    )

    assert source_count == 100
    assert len(first_train) == 80
    assert len(first_holdout) == 20
    assert first_train == second_train
    assert first_holdout == second_holdout
    assert Counter(row["native_lang_tag"] for row in first_holdout) == {
        "as-IN": 4,
        "bn-IN": 4,
        "bho-IN": 4,
        "ne-IN": 4,
        "or-IN": 4,
    }
    assert first_train[0]["input_mode"] == "audio"
    user_content = first_train[0]["messages"][0]["content"]
    assistant_content = first_train[0]["messages"][1]["content"]
    assert user_content[0]["type"] == "audio"
    assert user_content[1]["type"] == "text"
    assert assistant_content == [{"type": "text", "text": first_train[0]["target"]}]


def test_text_fallback_requires_and_uses_transcript_sidecar(tmp_path: Path) -> None:
    """Build text rows only when an explicit transcript sidecar is supplied."""
    LOGGER.info(
        "test_text_fallback_requires_and_uses_transcript_sidecar called temp_name=%s",
        tmp_path.name,
    )
    dummy_dir = tmp_path / "dummy"
    generate_dummy(dummy_dir, 100)
    config = load_config()

    train, holdout, _ = validate_and_prepare(
        dummy_dir / "corpus",
        dummy_dir,
        "text",
        dummy_dir / "transcripts.jsonl",
        config,
    )
    output = tmp_path / "prepared" / "train.jsonl"
    assert write_jsonl(output, train) == 80
    assert len(holdout) == 20
    assert train[0]["input_mode"] == "text"
    assert train[0]["messages"][0]["content"][0]["type"] == "text"
    assert train[0]["messages"][1]["content"][0]["text"] == train[0]["target"]
    assert output.is_file()


def test_audio_path_resolution_rejects_escape_from_data_root(tmp_path: Path) -> None:
    """Prevent prepared records from reading arbitrary files outside DATA_DIR."""
    LOGGER.info(
        "test_audio_path_resolution_rejects_escape_from_data_root called temp_name=%s",
        tmp_path.name,
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    escaped = tmp_path / "outside.flac"
    escaped.write_bytes(b"fLaC\x00fixture")
    record = {
        "utterance_id": "malicious-fixture",
        "audio_ref": {"clean_flac": "../outside.flac"},
    }

    with pytest.raises(ValueError, match="escapes DATA_DIR"):
        resolve_audio_path(record, data_dir)

