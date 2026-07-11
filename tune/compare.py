"""Print five held-out base-versus-adapter Gemma 4 outputs side by side.

Real inference uses lazy Transformers, PEFT, Torch, and optional audio loading.
The command does not silently replace audio with text. Dry-run validates the
holdout and adapter intent without downloading weights or using a GPU. Actual
predictions can be written to JSONL for the private metrics command.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from tune.config import TuneConfig, load_config
from tune.train import read_prepared_rows, validate_runtime_intent

LOGGER = logging.getLogger(__name__)


def select_samples(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Select the first deterministic prepared holdout samples."""
    LOGGER.info("select_samples called row_count=%d count=%d", len(rows), count)
    if len(rows) < count:
        raise ValueError(f"holdout needs at least {count} rows; found {len(rows)}")
    return rows[:count]


def load_inference_stack() -> tuple[Any, Any, Any, Any, Any]:
    """Import optional inference dependencies with a clear compatibility error."""
    LOGGER.info("load_inference_stack called")
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "Real comparison requires compatible torch, transformers, peft, accelerate, "
            "bitsandbytes, and the Gemma 4 processor. Native Windows is not assumed."
        ) from exc
    return torch, PeftModel, AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig


def generate_output(
    model: Any,
    processor: Any,
    messages: list[dict[str, Any]],
    max_new_tokens: int,
) -> str:
    """Generate one response from a prepared conversation using its native modality."""
    LOGGER.info("generate_output called message_count=%d max_new_tokens=%d", len(messages), max_new_tokens)
    prompt_messages = [messages[0]]
    try:
        inputs = processor.apply_chat_template(
            prompt_messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        )
        device = next(model.parameters()).device
        inputs = {name: value.to(device) for name, value in inputs.items()}
        generated = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        prompt_length = inputs["input_ids"].shape[-1]
        return processor.decode(generated[0][prompt_length:], skip_special_tokens=True).strip()
    except Exception as exc:
        raise RuntimeError(
            "Gemma 4 multimodal inference failed for the prepared conversation. Verify "
            "that the installed processor accepts local FLAC content items; no text "
            "substitution was performed."
        ) from exc


def compare_models(
    rows: list[dict[str, Any]],
    adapter: Path,
    config: TuneConfig,
) -> list[dict[str, str]]:
    """Generate base and tuned outputs for the selected holdout rows."""
    LOGGER.info(
        "compare_models called sample_count=%d adapter_name=%s model_id=%s",
        len(rows),
        adapter.name,
        config.model_id,
    )
    if not adapter.is_dir():
        raise ValueError(f"adapter directory does not exist: {adapter}")
    torch, PeftModel, AutoModel, AutoProcessor, BitsAndBytesConfig = load_inference_stack()
    quantization = BitsAndBytesConfig(load_in_4bit=True)
    processor = AutoProcessor.from_pretrained(config.model_id)
    base_model = AutoModel.from_pretrained(
        config.model_id,
        quantization_config=quantization,
        torch_dtype=torch.bfloat16,
        device_map="auto",
    )
    base_outputs = [
        generate_output(base_model, processor, row["messages"], config.max_new_tokens)
        for row in rows
    ]
    tuned_model = PeftModel.from_pretrained(base_model, str(adapter))
    tuned_outputs = [
        generate_output(tuned_model, processor, row["messages"], config.max_new_tokens)
        for row in rows
    ]
    return [
        {
            "utterance_id": row["utterance_id"],
            "target": row["target"],
            "base": base,
            "tuned": tuned,
        }
        for row, base, tuned in zip(rows, base_outputs, tuned_outputs, strict=True)
    ]


def print_comparison(results: list[dict[str, str]]) -> None:
    """Print stage-readable side-by-side qualitative outputs without percentages."""
    LOGGER.info("print_comparison called result_count=%d", len(results))
    for index, result in enumerate(results, start=1):
        print(f"\n[{index}] TARGET : {result['target']}")
        print(f"    BASE   : {result['base']}")
        print(f"    TUNED  : {result['tuned']}")


def write_predictions(path: Path, results: list[dict[str, str]]) -> None:
    """Write model outputs as private-metrics input JSONL."""
    LOGGER.info("write_predictions called output_name=%s result_count=%d", path.name, len(results))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    """Create the comparison command-line parser."""
    LOGGER.info("build_parser called")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate comparison inputs or run five base-versus-tuned generations."""
    LOGGER.info("main called argv_provided=%s", argv is not None)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    if not args.dry_run and args.adapter is None:
        raise SystemExit("--adapter is required unless --dry-run is used")
    config = load_config()
    rows = read_prepared_rows(args.holdout)
    mode = validate_runtime_intent(rows, config)
    selected = select_samples(rows, config.compare_samples)
    if args.dry_run:
        print(
            f"DRY RUN OK: compare_samples={len(selected)} mode={mode} "
            f"model={config.model_id}; no weights loaded"
        )
        return 0
    results = compare_models(rows, args.adapter, config)
    print_comparison(results[: config.compare_samples])
    if args.predictions is not None:
        write_predictions(args.predictions, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

