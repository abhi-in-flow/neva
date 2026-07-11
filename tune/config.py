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
    attention_implementation: str
    holdout_fraction: float
    split_seed: int
    lora_rank: int
    lora_alpha: int
    lora_dropout: float
    batch_size: int
    gradient_accumulation_steps: int
    epochs: float
    learning_rate: float
    warmup_steps: int
    weight_decay: float
    optimizer: str
    lr_scheduler_type: str
    max_grad_norm: float
    target_modules: tuple[str, ...]
    max_sequence_length: int
    max_new_tokens: int
    smoke_max_steps: int
    smoke_fixture_rows: int
    logging_steps: int
    save_steps: int
    min_free_vram_gib: float
    min_free_disk_gib: float
    fuzzy_threshold: float
    compare_samples: int
    compare_output_chars: int
    native_language_chars: int
    live_audio_min_seconds: float
    live_audio_max_seconds: float
    audio_tool_timeout_seconds: int
    dummy_rows: int


def load_config() -> TuneConfig:
    """Load tuning settings from safe environment values and validate them."""
    LOGGER.info("load_config called with TUNE_* environment overrides")
    config = TuneConfig(
        model_id=os.getenv(
            "TUNE_MODEL_ID",
            "unsloth/gemma-4-E4B-it-unsloth-bnb-4bit",
        ),
        attention_implementation=os.getenv("TUNE_ATTN_IMPLEMENTATION", "sdpa"),
        holdout_fraction=float(os.getenv("TUNE_HOLDOUT_FRACTION", "0.20")),
        split_seed=int(os.getenv("TUNE_SPLIT_SEED", "20260711")),
        lora_rank=int(os.getenv("TUNE_LORA_RANK", "16")),
        lora_alpha=int(os.getenv("TUNE_LORA_ALPHA", "16")),
        lora_dropout=float(os.getenv("TUNE_LORA_DROPOUT", "0")),
        batch_size=int(os.getenv("TUNE_BATCH_SIZE", "1")),
        gradient_accumulation_steps=int(os.getenv("TUNE_GRAD_ACCUM", "8")),
        epochs=float(os.getenv("TUNE_EPOCHS", "3")),
        learning_rate=float(os.getenv("TUNE_LEARNING_RATE", "0.0002")),
        warmup_steps=int(os.getenv("TUNE_WARMUP_STEPS", "5")),
        weight_decay=float(os.getenv("TUNE_WEIGHT_DECAY", "0.01")),
        optimizer=os.getenv("TUNE_OPTIMIZER", "adamw_8bit"),
        lr_scheduler_type=os.getenv("TUNE_LR_SCHEDULER", "cosine"),
        max_grad_norm=float(os.getenv("TUNE_MAX_GRAD_NORM", "0.3")),
        target_modules=(
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
            "post",
            "linear_start",
            "linear_end",
            "embedding_projection",
            "ffw_layer_1",
            "ffw_layer_2",
            "output_proj",
        ),
        max_sequence_length=int(os.getenv("TUNE_MAX_SEQUENCE_LENGTH", "2048")),
        max_new_tokens=int(os.getenv("TUNE_MAX_NEW_TOKENS", "64")),
        smoke_max_steps=int(os.getenv("TUNE_SMOKE_MAX_STEPS", "1")),
        smoke_fixture_rows=int(os.getenv("TUNE_SMOKE_FIXTURE_ROWS", "10")),
        logging_steps=int(os.getenv("TUNE_LOGGING_STEPS", "1")),
        save_steps=int(os.getenv("TUNE_SAVE_STEPS", "25")),
        min_free_vram_gib=float(os.getenv("TUNE_MIN_FREE_VRAM_GIB", "17")),
        min_free_disk_gib=float(os.getenv("TUNE_MIN_FREE_DISK_GIB", "40")),
        fuzzy_threshold=float(os.getenv("TUNE_FUZZY_THRESHOLD", "0.80")),
        compare_samples=int(os.getenv("TUNE_COMPARE_SAMPLES", "5")),
        compare_output_chars=int(os.getenv("TUNE_COMPARE_OUTPUT_CHARS", "500")),
        native_language_chars=int(os.getenv("TUNE_NATIVE_LANGUAGE_CHARS", "80")),
        live_audio_min_seconds=float(os.getenv("TUNE_LIVE_AUDIO_MIN_SECONDS", "1")),
        live_audio_max_seconds=float(os.getenv("TUNE_LIVE_AUDIO_MAX_SECONDS", "8")),
        audio_tool_timeout_seconds=int(os.getenv("TUNE_AUDIO_TOOL_TIMEOUT_SECONDS", "30")),
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
        "weight_decay": config.weight_decay,
        "max_grad_norm": config.max_grad_norm,
        "max_sequence_length": config.max_sequence_length,
        "max_new_tokens": config.max_new_tokens,
        "smoke_max_steps": config.smoke_max_steps,
        "smoke_fixture_rows": config.smoke_fixture_rows,
        "logging_steps": config.logging_steps,
        "save_steps": config.save_steps,
        "min_free_vram_gib": config.min_free_vram_gib,
        "min_free_disk_gib": config.min_free_disk_gib,
        "compare_samples": config.compare_samples,
        "compare_output_chars": config.compare_output_chars,
        "native_language_chars": config.native_language_chars,
        "live_audio_min_seconds": config.live_audio_min_seconds,
        "live_audio_max_seconds": config.live_audio_max_seconds,
        "audio_tool_timeout_seconds": config.audio_tool_timeout_seconds,
        "dummy_rows": config.dummy_rows,
    }
    invalid = [name for name, value in positive_values.items() if value <= 0]
    if invalid:
        raise ValueError(f"configuration values must be positive: {', '.join(invalid)}")
    if config.lora_dropout != 0:
        raise ValueError("E4B QLoRA profile requires TUNE_LORA_DROPOUT=0")
    if config.warmup_steps < 0:
        raise ValueError("TUNE_WARMUP_STEPS must be non-negative")
    if config.attention_implementation not in {"sdpa", "eager"}:
        raise ValueError("TUNE_ATTN_IMPLEMENTATION must be 'sdpa' or 'eager'")
    if not config.optimizer.strip():
        raise ValueError("TUNE_OPTIMIZER must not be empty")
    if not config.lr_scheduler_type.strip():
        raise ValueError("TUNE_LR_SCHEDULER must not be empty")
    if not config.target_modules:
        raise ValueError("audio QLoRA target modules must not be empty")
    if not 0.0 <= config.fuzzy_threshold <= 1.0:
        raise ValueError("TUNE_FUZZY_THRESHOLD must be between 0 and 1")
    if config.live_audio_min_seconds >= config.live_audio_max_seconds:
        raise ValueError("live audio minimum duration must be below maximum duration")

