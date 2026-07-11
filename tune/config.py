"""Central configuration for the isolated Gemma 4 E4B QLoRA harness.

All model identifiers, split behavior, generation limits, adapter parameters,
and output names live here. Values may be overridden with ``TUNE_*``
environment variables without importing the application configuration.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TuneConfig:
    """Describe reproducible preparation, training, and evaluation settings."""

    model_id: str
    holdout_fraction: float
    split_seed: int
    lora_rank: int
    lora_alpha: int
    lora_dropout: float
    batch_size: int
    gradient_accumulation_steps: int
    epochs: float
    learning_rate: float
    max_sequence_length: int
    max_new_tokens: int
    fuzzy_threshold: float
    compare_samples: int
    dummy_rows: int


def load_config() -> TuneConfig:
    """Load tuning settings from safe environment values and validate them."""
    LOGGER.info("load_config called with TUNE_* environment overrides")
    config = TuneConfig(
        model_id=os.getenv("TUNE_MODEL_ID", "unsloth/gemma-4-e4b"),
        holdout_fraction=float(os.getenv("TUNE_HOLDOUT_FRACTION", "0.20")),
        split_seed=int(os.getenv("TUNE_SPLIT_SEED", "20260711")),
        lora_rank=int(os.getenv("TUNE_LORA_RANK", "16")),
        lora_alpha=int(os.getenv("TUNE_LORA_ALPHA", "16")),
        lora_dropout=float(os.getenv("TUNE_LORA_DROPOUT", "0")),
        batch_size=int(os.getenv("TUNE_BATCH_SIZE", "1")),
        gradient_accumulation_steps=int(os.getenv("TUNE_GRAD_ACCUM", "8")),
        epochs=float(os.getenv("TUNE_EPOCHS", "3")),
        learning_rate=float(os.getenv("TUNE_LEARNING_RATE", "0.0002")),
        max_sequence_length=int(os.getenv("TUNE_MAX_SEQUENCE_LENGTH", "2048")),
        max_new_tokens=int(os.getenv("TUNE_MAX_NEW_TOKENS", "64")),
        fuzzy_threshold=float(os.getenv("TUNE_FUZZY_THRESHOLD", "0.80")),
        compare_samples=int(os.getenv("TUNE_COMPARE_SAMPLES", "5")),
        dummy_rows=int(os.getenv("TUNE_DUMMY_ROWS", "100")),
    )
    validate_config(config)
    return config


def validate_config(config: TuneConfig) -> None:
    """Reject unsafe or internally inconsistent tuning configuration values."""
    LOGGER.info(
        "validate_config called model_id=%s rank=%d batch_size=%d epochs=%s",
        config.model_id,
        config.lora_rank,
        config.batch_size,
        config.epochs,
    )
    if not config.model_id.strip():
        raise ValueError("TUNE_MODEL_ID must not be empty")
    if not 0.0 < config.holdout_fraction < 1.0:
        raise ValueError("TUNE_HOLDOUT_FRACTION must be between 0 and 1")
    positive_values = {
        "lora_rank": config.lora_rank,
        "lora_alpha": config.lora_alpha,
        "batch_size": config.batch_size,
        "gradient_accumulation_steps": config.gradient_accumulation_steps,
        "epochs": config.epochs,
        "learning_rate": config.learning_rate,
        "max_sequence_length": config.max_sequence_length,
        "max_new_tokens": config.max_new_tokens,
        "compare_samples": config.compare_samples,
        "dummy_rows": config.dummy_rows,
    }
    invalid = [name for name, value in positive_values.items() if value <= 0]
    if invalid:
        raise ValueError(f"configuration values must be positive: {', '.join(invalid)}")
    if config.lora_dropout != 0:
        raise ValueError("E4B QLoRA profile requires TUNE_LORA_DROPOUT=0")
    if not 0.0 <= config.fuzzy_threshold <= 1.0:
        raise ValueError("TUNE_FUZZY_THRESHOLD must be between 0 and 1")

