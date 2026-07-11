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
import tempfile
from pathlib import Path
from typing import Any

from tune.config import TuneConfig, load_config
from tune.events import JsonlEventWriter, bounded_text, write_result
from tune.live import build_live_row, normalize_live_audio, probe_live_audio_duration
from tune.manifest import (
    load_manifest,
    validate_artifact_compatibility,
    validate_dataset_files,
)
from tune.train import read_prepared_rows, validate_runtime_intent

LOGGER = logging.getLogger(__name__)
INFERENCE_AUDIO_KEYS = frozenset({"input_features", "input_features_mask"})


def select_samples(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Select the first deterministic prepared holdout samples."""
    LOGGER.info("select_samples called row_count=%d count=%d", len(rows), count)
    if len(rows) < count:
        raise ValueError(f"holdout needs at least {count} rows; found {len(rows)}")
    return rows[:count]


def load_inference_stack() -> tuple[Any, Any]:
    """Import optional inference dependencies with a clear compatibility error."""
    LOGGER.info("load_inference_stack called")
    try:
        from unsloth import FastVisionModel

        import torch
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "Real comparison requires the isolated CUDA Torch and Unsloth environment."
        ) from exc
    return torch, FastVisionModel


def verify_inference_audio_inputs(inputs: Any) -> None:
    """Fail unless the processor encoded attached audio for model inference.

    Args:
        inputs: Processor batch mapping produced from one audio-first prompt.

    Raises:
        RuntimeError: If required audio feature tensors are absent.
    """
    keys = set(inputs.keys())
    LOGGER.info("verify_inference_audio_inputs called keys=%s", sorted(keys))
    missing = INFERENCE_AUDIO_KEYS.difference(keys)
    if missing:
        raise RuntimeError(
            "processor did not encode attached audio; missing " + ", ".join(sorted(missing))
        )


def generate_output(
    model: Any,
    processor: Any,
    messages: list[dict[str, Any]],
    max_new_tokens: int,
    max_sequence_length: int,
) -> str:
    """Generate one response from a prepared conversation using its native modality.

    Args:
        model: Loaded base or adapter-backed Gemma model.
        processor: Matching multimodal processor.
        messages: One prepared user/assistant conversation.
        max_new_tokens: Bounded response-token count.
        max_sequence_length: Required audio truncation bound for the processor.

    Returns:
        Decoded generated assistant response.
    """
    LOGGER.info(
        "generate_output called message_count=%d max_new_tokens=%d max_sequence_length=%d",
        len(messages),
        max_new_tokens,
        max_sequence_length,
    )
    prompt_messages = [messages[0]]
    try:
        inputs = processor.apply_chat_template(
            prompt_messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            processor_kwargs={
                "audio_kwargs": {
                    "max_length": max_sequence_length,
                }
            },
        )
        verify_inference_audio_inputs(inputs)
        device = next(model.parameters()).device
        inputs = inputs.to(device)
        generated = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        prompt_length = inputs["input_ids"].shape[-1]
        decoded = processor.decode(generated[0][prompt_length:], skip_special_tokens=False)
        parse_response = getattr(processor, "parse_response", None)
        if callable(parse_response):
            parsed = parse_response(decoded)
            if isinstance(parsed, dict) and isinstance(parsed.get("content"), str):
                return parsed["content"].strip()
            return str(parsed).strip()
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
    *,
    event_writer: JsonlEventWriter | None = None,
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
    events = event_writer or JsonlEventWriter(None)
    events.emit("loading_base", 0.2, "Loading the configured base model")
    torch, FastVisionModel = load_inference_stack()
    base_model, base_processor = FastVisionModel.from_pretrained(
        model_name=config.model_id,
        max_seq_length=config.max_sequence_length,
        load_in_4bit=True,
        attn_implementation=config.attention_implementation,
    )
    for_inference = getattr(FastVisionModel, "for_inference", None)
    if callable(for_inference):
        for_inference(base_model)
    events.emit("base_inference", 0.35, "Generating bounded base-model output")
    base_outputs = [
        generate_output(
            base_model,
            base_processor,
            row["messages"],
            config.max_new_tokens,
            config.max_sequence_length,
        )
        for row in rows
    ]
    del base_model
    torch.cuda.empty_cache()
    events.emit("loading_adapter", 0.55, "Loading the verified tuned adapter")
    tuned_model, tuned_processor = FastVisionModel.from_pretrained(
        model_name=str(adapter),
        max_seq_length=config.max_sequence_length,
        load_in_4bit=True,
        attn_implementation=config.attention_implementation,
    )
    if callable(for_inference):
        for_inference(tuned_model)
    events.emit("tuned_inference", 0.75, "Generating bounded tuned-model output")
    tuned_outputs = [
        generate_output(
            tuned_model,
            tuned_processor,
            row["messages"],
            config.max_new_tokens,
            config.max_sequence_length,
        )
        for row in rows
    ]
    return [
        {
            "utterance_id": row["utterance_id"],
            "target": bounded_text(row["target"], config.compare_output_chars),
            "audio_path": row["messages"][0]["content"][0]["audio"],
            "base": bounded_text(base, config.compare_output_chars),
            "tuned": bounded_text(tuned, config.compare_output_chars),
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
    parser.add_argument(
        "--dataset-manifest",
        type=Path,
        help="Frozen dataset manifest; defaults beside holdout.jsonl.",
    )
    parser.add_argument(
        "--artifact-manifest",
        type=Path,
        help="Completed adapter manifest; defaults beside the adapter directory.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        help="Comparison count up to configured maximum; defaults to five.",
    )
    parser.add_argument("--predictions", type=Path)
    parser.add_argument(
        "--live-audio",
        type=Path,
        help="One temporary recording for inference only; never added to the corpus.",
    )
    parser.add_argument("--native-language", help="Declared language for --live-audio.")
    parser.add_argument("--events", type=Path, help="Optional safe progress JSONL destination.")
    parser.add_argument("--result", type=Path, help="Optional structured result JSON destination.")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate comparison inputs or run five base-versus-tuned generations."""
    LOGGER.info("main called argv_provided=%s", argv is not None)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    if not args.dry_run and args.adapter is None:
        raise SystemExit("--adapter is required unless --dry-run is used")
    if (args.live_audio is None) != (args.native_language is None):
        raise SystemExit("--live-audio and --native-language must be provided together")
    config = load_config()
    events = JsonlEventWriter(args.events)
    events.emit("validating", 0.05, "Validating comparison inputs")
    rows = read_prepared_rows(args.holdout)
    mode = validate_runtime_intent(rows, config)
    sample_count = args.samples if args.samples is not None else config.compare_samples
    if sample_count <= 0 or sample_count > config.compare_samples:
        raise SystemExit(f"--samples must be between 1 and {config.compare_samples}")
    selected = select_samples(rows, sample_count) if args.live_audio is None else []
    dataset_manifest_path = args.dataset_manifest or args.holdout.parent / "dataset_manifest.json"
    dataset_manifest = load_manifest(dataset_manifest_path)
    validate_dataset_files(
        dataset_manifest,
        args.holdout.parent / "train.jsonl",
        args.holdout,
    )
    if args.dry_run:
        dry_count = 1 if args.live_audio is not None else len(selected)
        print(
            f"DRY RUN OK: compare_samples={dry_count} mode={mode} "
            f"model={config.model_id}; no weights loaded"
        )
        return 0
    artifact_manifest_path = (
        args.artifact_manifest or args.adapter.parent / "artifact_manifest.json"
    )
    artifact_manifest = load_manifest(artifact_manifest_path)
    validate_artifact_compatibility(dataset_manifest, artifact_manifest, args.adapter)
    if args.live_audio is not None:
        with tempfile.TemporaryDirectory(prefix="gemma-live-compare-") as temporary:
            normalized = Path(temporary) / "live.flac"
            events.emit("normalizing_audio", 0.1, "Normalizing temporary live audio")
            normalize_live_audio(args.live_audio, normalized, config)
            duration_seconds = probe_live_audio_duration(normalized, config)
            events.emit(
                "validated_audio",
                0.15,
                "Validated temporary live audio duration",
                duration_seconds=round(duration_seconds, 3),
            )
            selected = [build_live_row(normalized, args.native_language, config)]
            results = compare_models(
                selected,
                args.adapter,
                config,
                event_writer=events,
            )
    else:
        results = compare_models(
            selected,
            args.adapter,
            config,
            event_writer=events,
        )
    print_comparison(results)
    if args.predictions is not None:
        write_predictions(args.predictions, results)
    public_results = [
        {
            "utterance_id": result["utterance_id"],
            "target": result["target"],
            "base": result["base"],
            "tuned": result["tuned"],
        }
        for result in results
    ]
    result_payload: dict[str, Any] = {
        "status": "completed",
        "kind": "infer_live" if args.live_audio is not None else "compare",
        "model_id": config.model_id,
        "sample_count": len(public_results),
        "predictions": public_results,
    }
    if args.live_audio is not None:
        result_payload["base_output"] = public_results[0]["base"]
        result_payload["tuned_output"] = public_results[0]["tuned"]
    write_result(args.result, result_payload)
    events.emit(
        "completed",
        1.0,
        "Comparison completed",
        sample_count=len(public_results),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

