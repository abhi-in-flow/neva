"""Dependency-light tests for smoke/full controls and fail-closed audio training."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tune.train import validate_output_path, verify_audio_batch

LOGGER = logging.getLogger(__name__)


class FakeBoolean:
    """Provide the minimal ``item`` protocol used by batch verification."""

    def __init__(self, value: bool) -> None:
        """Store a deterministic synthetic tensor-reduction result."""
        LOGGER.info("FakeBoolean.__init__ called value=%s", value)
        self.value = value

    def item(self) -> bool:
        """Return the stored boolean result."""
        LOGGER.info("FakeBoolean.item called")
        return self.value


class FakeLabels:
    """Provide equality and reduction operations without importing Torch."""

    def __eq__(self, value: object) -> "FakeLabels":
        """Return this fixture for the expected ignore-index comparison."""
        LOGGER.info("FakeLabels.__eq__ called value=%s", value)
        return self

    def any(self) -> FakeBoolean:
        """Report that at least one synthetic label is masked."""
        LOGGER.info("FakeLabels.any called")
        return FakeBoolean(True)


class FakeTorch:
    """Expose only the tensor predicate required by ``verify_audio_batch``."""

    @staticmethod
    def is_tensor(value: object) -> bool:
        """Treat the dedicated fake labels object as a tensor."""
        LOGGER.info("FakeTorch.is_tensor called type=%s", type(value).__name__)
        return isinstance(value, FakeLabels)


def test_audio_batch_verification_fails_without_encoder_features() -> None:
    """Reject the historical silent text-only collator behavior."""
    LOGGER.info("test_audio_batch_verification_fails_without_encoder_features called")
    with pytest.raises(RuntimeError, match="input_features"):
        verify_audio_batch({"labels": FakeLabels()}, FakeTorch)


def test_audio_batch_verification_accepts_features_and_masked_labels() -> None:
    """Accept a collated batch only when audio tensors and masked labels exist."""
    LOGGER.info("test_audio_batch_verification_accepts_features_and_masked_labels called")
    verify_audio_batch(
        {
            "input_features": object(),
            "input_features_mask": object(),
            "labels": FakeLabels(),
        },
        FakeTorch,
    )


def test_training_output_refuses_input_overlap_and_unplanned_overwrite(tmp_path: Path) -> None:
    """Protect prepared data and existing artifacts from accidental replacement."""
    LOGGER.info(
        "test_training_output_refuses_input_overlap_and_unplanned_overwrite called temp_name=%s",
        tmp_path.name,
    )
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    train = prepared / "train.jsonl"
    train.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="overlap"):
        validate_output_path(prepared, train, None)

    output = tmp_path / "run"
    output.mkdir()
    (output / "existing.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        validate_output_path(output, train, None)
