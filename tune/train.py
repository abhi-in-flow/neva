"""Run optional Gemma 4 E4B QLoRA while keeping dry runs dependency-light.

Dry-run mode validates configuration and prepared conversational JSONL without
importing Torch, Unsloth, Transformers, TRL, PEFT, or Datasets. Real training
loads those packages lazily and saves an adapter only after ``trainer.train``
returns successfully. Audio conversations are passed through unchanged to
Unsloth/TRL; if the installed versions do not support Gemma 4 audio SFT, the
command fails with an explicit compatibility error instead of switching modes.
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
from pathlib import Path
from typing import Any

from tune.config import TuneConfig, load_config

LOGGER = logging.getLogger(__name__)


def read_prepared_rows(path: Path) -> list[dict[str, Any]]:
    """Read and validate prepared SFT conversations without model dependencies."""
    LOGGER.info("read_prepared_rows called input_name=%s", path.name)
    if not path.is_file():
        raise ValueError(f"prepared training file does not exist: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path.name}:{line_number}") from exc
            messages = row.get("messages") if isinstance(row, dict) else None
            mode = row.get("input_mode") if isinstance(row, dict) else None
            if mode not in {"audio", "text"} or not isinstance(messages, list) or len(messages) != 2:
                raise ValueError(f"invalid prepared row at {path.name}:{line_number}")
            rows.append(row)
    if not rows:
        raise ValueError("prepared training file contains no rows")
    modes = {row["input_mode"] for row in rows}
    if len(modes) != 1:
        raise ValueError("prepared training file mixes audio and text modes")
    return rows


def validate_runtime_intent(rows: list[dict[str, Any]], config: TuneConfig) -> str:
    """Validate mode-specific structure and return the single prepared input mode."""
    mode = rows[0]["input_mode"]
    LOGGER.info(
        "validate_runtime_intent called row_count=%d mode=%s model_id=%s",
        len(rows),
        mode,
        config.model_id,
    )
    if mode == "audio":
        for row in rows:
            content = row["messages"][0].get("content")
            audio_items = (
                [item for item in content if isinstance(item, dict) and item.get("type") == "audio"]
                if isinstance(content, list)
                else []
            )
            if len(audio_items) != 1:
                raise ValueError("audio rows require exactly one audio content item")
    return mode


def load_training_stack() -> tuple[Any, Any, Any, Any]:
    """Import optional training libraries and explain installation failures."""
    LOGGER.info("load_training_stack called platform=%s", platform.system())
    try:
        from datasets import Dataset
        from trl import SFTConfig, SFTTrainer
        from unsloth import FastModel
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "Real training requires compatible torch, unsloth, transformers, trl, peft, "
            "accelerate, bitsandbytes, and datasets installations. Native Windows support "
            "is not assumed; use WSL2/Linux if Unsloth or bitsandbytes cannot load."
        ) from exc
    return Dataset, SFTConfig, SFTTrainer, FastModel


def run_training(rows: list[dict[str, Any]], output: Path, config: TuneConfig) -> None:
    """Train and save an E4B QLoRA adapter, propagating audio-support failures."""
    mode = validate_runtime_intent(rows, config)
    LOGGER.info(
        "run_training called row_count=%d output_name=%s mode=%s model_id=%s",
        len(rows),
        output.name,
        mode,
        config.model_id,
    )
    Dataset, SFTConfig, SFTTrainer, FastModel = load_training_stack()
    try:
        model, processor = FastModel.from_pretrained(
            model_name=config.model_id,
            max_seq_length=config.max_sequence_length,
            load_in_4bit=True,
        )
        model = FastModel.get_peft_model(
            model,
            r=config.lora_rank,
            lora_alpha=config.lora_alpha,
            lora_dropout=config.lora_dropout,
            bias="none",
            use_gradient_checkpointing="unsloth",
            random_state=config.split_seed,
        )
        training_args = SFTConfig(
            output_dir=str(output / "checkpoints"),
            per_device_train_batch_size=config.batch_size,
            gradient_accumulation_steps=config.gradient_accumulation_steps,
            num_train_epochs=config.epochs,
            learning_rate=config.learning_rate,
            max_seq_length=config.max_sequence_length,
            bf16=True,
            logging_steps=1,
            save_strategy="epoch",
            report_to="none",
            seed=config.split_seed,
        )
        trainer = SFTTrainer(
            model=model,
            processing_class=processor,
            train_dataset=Dataset.from_list(rows),
            args=training_args,
        )
        trainer.train()
        adapter_dir = output / "adapter"
        model.save_pretrained(str(adapter_dir))
        processor.save_pretrained(str(adapter_dir))
    except Exception as exc:
        raise RuntimeError(
            f"E4B {mode} QLoRA did not complete. No success should be claimed and no "
            "fallback was selected automatically. Check the installed Unsloth/TRL Gemma 4 "
            "multimodal dataset support and the preceding exception."
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    """Create the training command-line parser."""
    LOGGER.info("build_parser called")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate a run or perform optional real QLoRA training."""
    LOGGER.info("main called argv_provided=%s", argv is not None)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    if not args.dry_run and args.output is None:
        raise SystemExit("--output is required unless --dry-run is used")
    config = load_config()
    rows = read_prepared_rows(args.train)
    mode = validate_runtime_intent(rows, config)
    if args.dry_run:
        print(
            f"DRY RUN OK: rows={len(rows)} mode={mode} model={config.model_id} "
            f"rank={config.lora_rank} batch={config.batch_size} "
            f"grad_accum={config.gradient_accumulation_steps} epochs={config.epochs}"
        )
        return 0
    run_training(rows, args.output, config)
    print(f"TRAINING COMPLETED: adapter={args.output / 'adapter'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

