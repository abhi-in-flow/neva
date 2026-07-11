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
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tune.config import TuneConfig, load_config
from tune.manifest import (
    build_artifact_manifest,
    load_manifest,
    validate_dataset_files,
    write_manifest,
)

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
    for row in rows:
        user_content = row["messages"][0].get("content")
        assistant_content = row["messages"][1].get("content")
        if not isinstance(user_content, list) or not user_content:
            raise ValueError("prepared rows require typed user content")
        if (
            not isinstance(assistant_content, list)
            or len(assistant_content) != 1
            or assistant_content[0].get("type") != "text"
            or not assistant_content[0].get("text")
        ):
            raise ValueError("prepared rows require one typed assistant text target")
        if mode == "audio":
            if len(user_content) != 2:
                raise ValueError("audio rows require audio followed by instruction text")
            audio_item, text_item = user_content
            if (
                audio_item.get("type") != "audio"
                or not audio_item.get("audio")
                or text_item.get("type") != "text"
                or not text_item.get("text")
            ):
                raise ValueError("audio rows require audio followed by instruction text")
        elif len(user_content) != 1 or user_content[0].get("type") != "text":
            raise ValueError("text rows require one typed text content item")
    return mode


def load_training_stack() -> tuple[Any, Any, Any, Any, Any, Any]:
    """Import optional training libraries and explain installation failures."""
    LOGGER.info("load_training_stack called platform=%s", platform.system())
    try:
        from unsloth import FastVisionModel

        import torch
        from datasets import Dataset
        from trl import SFTConfig, SFTTrainer
        from unsloth.trainer import UnslothVisionDataCollator
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "Real training requires compatible torch, unsloth, transformers, trl, peft, "
            "accelerate, bitsandbytes, and datasets in the isolated tune environment."
        ) from exc
    return Dataset, SFTConfig, SFTTrainer, FastVisionModel, UnslothVisionDataCollator, torch


def validate_output_path(
    output: Path,
    train_path: Path,
    resume_from_checkpoint: Path | None,
) -> Path:
    """Reject artifact paths that could overwrite prepared or runtime input.

    Args:
        output: Requested artifact directory.
        train_path: Read-only prepared training JSONL.
        resume_from_checkpoint: Optional existing checkpoint directory.

    Returns:
        Resolved safe output path.

    Raises:
        ValueError: If output overlaps input, is a symlink, or is unexpectedly
            non-empty without explicit resume intent.
    """
    LOGGER.info(
        "validate_output_path called output_name=%s train_name=%s resume=%s",
        output.name,
        train_path.name,
        resume_from_checkpoint is not None,
    )
    resolved_output = output.resolve()
    resolved_train = train_path.resolve()
    if output.is_symlink():
        raise ValueError("training output must not be a symbolic link")
    if resolved_output == resolved_train.parent or resolved_output in resolved_train.parents:
        raise ValueError("training output must not overlap prepared input")
    if resolved_output.exists() and any(resolved_output.iterdir()) and resume_from_checkpoint is None:
        raise ValueError("training output must be empty unless --resume-from-checkpoint is used")
    if resume_from_checkpoint is not None:
        checkpoint = resume_from_checkpoint.resolve()
        if not checkpoint.is_dir() or resolved_output not in checkpoint.parents:
            raise ValueError("resume checkpoint must exist under the selected output directory")
    return resolved_output


def verify_audio_batch(batch: dict[str, Any], torch: Any) -> None:
    """Fail closed unless the collator emitted real audio encoder features.

    Args:
        batch: One batch produced by the installed Unsloth audio-aware collator.
        torch: Lazily imported Torch module.

    Raises:
        RuntimeError: If audio tensors or masked labels are missing.
    """
    LOGGER.info("verify_audio_batch called keys=%s", sorted(batch))
    required = {"input_features", "input_features_mask", "labels"}
    missing = required.difference(batch)
    if missing:
        raise RuntimeError(
            "audio-aware collator verification failed; missing " + ", ".join(sorted(missing))
        )
    labels = batch["labels"]
    if not torch.is_tensor(labels) or not bool((labels == -100).any().item()):
        raise RuntimeError("audio-aware collator did not mask non-target tokens in labels")


def write_training_metrics(path: Path, payload: dict[str, Any]) -> None:
    """Write private, reproducible training metrics with restrictive permissions.

    Args:
        path: Metrics JSON destination under the selected artifact directory.
        payload: Non-secret run metadata and trainer metrics.

    Side effects:
        Creates one JSON file and attempts to restrict it to the current user.
    """
    LOGGER.info("write_training_metrics called path_name=%s keys=%s", path.name, sorted(payload))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        LOGGER.warning("write_training_metrics could not set restrictive permissions")


def run_training(
    rows: list[dict[str, Any]],
    output: Path,
    config: TuneConfig,
    *,
    max_steps: int | None = None,
    resume_from_checkpoint: Path | None = None,
) -> dict[str, Any]:
    """Train and save an E4B audio QLoRA adapter after collator verification.

    Args:
        rows: Prepared, single-mode conversational examples.
        output: Empty or explicitly resumed artifact directory.
        config: Centralized model and optimization configuration.
        max_steps: Explicit optimizer-step cap; one step is the smoke profile.
        resume_from_checkpoint: Optional checkpoint beneath ``output``.

    Returns:
        Private timing, VRAM, profile, and trainer metrics.
    """
    mode = validate_runtime_intent(rows, config)
    LOGGER.info(
        "run_training called row_count=%d output_name=%s mode=%s model_id=%s max_steps=%s resume=%s",
        len(rows),
        output.name,
        mode,
        config.model_id,
        max_steps,
        resume_from_checkpoint is not None,
    )
    if mode != "audio":
        raise RuntimeError("this Gemma 4 run is audio-only; text fallback requires a separate go/no-go")
    if max_steps is not None and max_steps <= 0:
        raise ValueError("max_steps must be positive when provided")
    Dataset, SFTConfig, SFTTrainer, FastVisionModel, Collator, torch = load_training_stack()
    started = time.monotonic()
    try:
        torch.cuda.reset_peak_memory_stats()
        model, processor = FastVisionModel.from_pretrained(
            model_name=config.model_id,
            max_seq_length=config.max_sequence_length,
            load_in_4bit=True,
            full_finetuning=False,
            use_gradient_checkpointing="unsloth",
            attn_implementation=config.attention_implementation,
        )
        model = FastVisionModel.get_peft_model(
            model,
            r=config.lora_rank,
            lora_alpha=config.lora_alpha,
            lora_dropout=config.lora_dropout,
            bias="none",
            target_modules=list(config.target_modules),
            finetune_vision_layers=False,
            finetune_language_layers=True,
            finetune_attention_modules=True,
            finetune_mlp_modules=True,
            use_gradient_checkpointing="unsloth",
            random_state=config.split_seed,
            use_rslora=False,
            loftq_config=None,
        )
        for_training = getattr(FastVisionModel, "for_training", None)
        if callable(for_training):
            for_training(model)
        collator = Collator(model, processor, max_seq_length=config.max_sequence_length)
        verify_audio_batch(collator([rows[0]]), torch)
        training_args = SFTConfig(
            output_dir=str(output / "checkpoints"),
            per_device_train_batch_size=config.batch_size,
            gradient_accumulation_steps=config.gradient_accumulation_steps,
            num_train_epochs=config.epochs,
            max_steps=max_steps if max_steps is not None else -1,
            learning_rate=config.learning_rate,
            warmup_steps=config.warmup_steps,
            weight_decay=config.weight_decay,
            optim=config.optimizer,
            lr_scheduler_type=config.lr_scheduler_type,
            max_grad_norm=config.max_grad_norm,
            max_length=None,
            bf16=True,
            fp16=False,
            logging_steps=config.logging_steps,
            save_strategy="steps" if max_steps is not None else "epoch",
            save_steps=config.save_steps,
            report_to="none",
            seed=config.split_seed,
            remove_unused_columns=False,
            dataset_text_field="",
            dataset_kwargs={"skip_prepare_dataset": True},
        )
        trainer = SFTTrainer(
            model=model,
            processing_class=processor.tokenizer,
            train_dataset=Dataset.from_list(rows),
            data_collator=collator,
            args=training_args,
        )
        train_result = trainer.train(
            resume_from_checkpoint=str(resume_from_checkpoint)
            if resume_from_checkpoint is not None
            else None
        )
        adapter_dir = output / "adapter"
        model.save_pretrained(str(adapter_dir))
        processor.save_pretrained(str(adapter_dir))
        duration_seconds = time.monotonic() - started
        metrics = {
            "status": "completed",
            "profile": "smoke" if max_steps is not None else "full",
            "model_id": config.model_id,
            "input_mode": mode,
            "sample_count": len(rows),
            "max_steps": max_steps,
            "duration_seconds": round(duration_seconds, 3),
            "peak_vram_gib": round(torch.cuda.max_memory_allocated() / (1024**3), 3),
            "adapter_path": str(adapter_dir.resolve()),
            "trainer_metrics": dict(train_result.metrics),
            "config": asdict(config),
        }
        write_training_metrics(output / "training_metrics.json", metrics)
        return metrics
    except Exception as exc:
        raise RuntimeError(
            f"E4B {mode} QLoRA did not complete. No success should be claimed and no "
            "fallback was selected automatically. Verify that the installed Unsloth collator "
            "emits audio features and inspect the preceding exception."
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    """Create the training command-line parser."""
    LOGGER.info("build_parser called")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--dataset-manifest",
        type=Path,
        help="Frozen preparation manifest; defaults beside train.jsonl.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        help="Explicit optimizer-step cap; use 1 for the isolated GPU smoke run.",
    )
    parser.add_argument("--resume-from-checkpoint", type=Path)
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
    dataset_manifest_path = args.dataset_manifest or args.train.parent / "dataset_manifest.json"
    dataset_manifest = load_manifest(dataset_manifest_path)
    validate_dataset_files(
        dataset_manifest,
        args.train,
        args.train.parent / "holdout.jsonl",
    )
    if dataset_manifest.get("model_id") != config.model_id:
        raise SystemExit("dataset manifest model does not match current TUNE_MODEL_ID")
    if args.max_steps is not None and args.max_steps <= 0:
        raise SystemExit("--max-steps must be positive")
    if args.dry_run:
        profile = "smoke" if args.max_steps is not None else "full"
        print(
            f"DRY RUN OK: profile={profile} rows={len(rows)} mode={mode} model={config.model_id} "
            f"rank={config.lora_rank} batch={config.batch_size} "
            f"grad_accum={config.gradient_accumulation_steps} epochs={config.epochs} "
            f"max_steps={args.max_steps}"
        )
        return 0
    output = validate_output_path(args.output, args.train, args.resume_from_checkpoint)
    metrics = run_training(
        rows,
        output,
        config,
        max_steps=args.max_steps,
        resume_from_checkpoint=args.resume_from_checkpoint,
    )
    artifact_manifest = build_artifact_manifest(
        dataset_manifest,
        dataset_manifest_path,
        metrics,
        output / "adapter",
        config,
    )
    write_manifest(output / "artifact_manifest.json", artifact_manifest)
    print(f"TRAINING COMPLETED: adapter={args.output / 'adapter'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

