"""Prepare golden JSONL shards for isolated Gemma 4 speech-to-text SFT.

The default, shipped representation is audio-first: each conversational row
contains a clean FLAC input and the system-owned common-language label as the
assistant target. A text fallback is explicit and requires a caller-provided
JSONL sidecar of ``{"utterance_id": ..., "transcript": ...}`` rows; the
canonical golden record is never modified. The deterministic holdout uses a
largest-remainder allocation across native-language strata to preserve an
overall 20 percent split without random global sampling.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from tune.config import TuneConfig, load_config

LOGGER = logging.getLogger(__name__)
ELIGIBILITY_TRUE_FIELDS = ("is_speech", "single_speaker", "audio_quality_ok")
TASK_TEMPLATE = "Translate this speech from {native_language} into the common language."
TEXT_TASK_TEMPLATE = (
    "Translate this transcribed speech from {native_language} into the common language:\n"
    "{transcript}"
)


def discover_shards(corpus_path: Path) -> list[Path]:
    """Return sorted JSONL shards from a file or corpus directory."""
    LOGGER.info("discover_shards called corpus_name=%s", corpus_path.name)
    if corpus_path.is_file():
        return [corpus_path]
    if not corpus_path.is_dir():
        raise ValueError(f"corpus path does not exist: {corpus_path}")
    shards = sorted(corpus_path.glob("*.jsonl"))
    if not shards:
        raise ValueError(f"no JSONL shards found in: {corpus_path}")
    return shards


def read_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Read JSON objects from JSONL files with source-aware validation errors."""
    paths = list(paths)
    LOGGER.info("read_jsonl called file_count=%d", len(paths))
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON in {path.name}:{line_number}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"expected object in {path.name}:{line_number}")
                rows.append(row)
    return rows


def is_training_eligible(record: dict[str, Any]) -> bool:
    """Recompute all frozen golden-record eligibility gates defensively."""
    LOGGER.info("is_training_eligible called utterance_id=%s", record.get("utterance_id"))
    quality = record.get("quality")
    validation = record.get("validation")
    if not isinstance(quality, dict) or not isinstance(validation, dict):
        return False
    if record.get("training_eligible") is False:
        return False
    return (
        all(quality.get(field) is True for field in ELIGIBILITY_TRUE_FIELDS)
        and quality.get("contamination_flag") is False
        and quality.get("duplicate") is False
        and validation.get("correct") is True
    )


def resolve_audio_path(record: dict[str, Any], data_dir: Path) -> Path:
    """Resolve a clean FLAC reference under DATA_DIR and reject path traversal."""
    LOGGER.info("resolve_audio_path called utterance_id=%s", record.get("utterance_id"))
    audio_ref = record.get("audio_ref")
    relative = audio_ref.get("clean_flac") if isinstance(audio_ref, dict) else None
    if not isinstance(relative, str) or not relative:
        raise ValueError("eligible record is missing audio_ref.clean_flac")
    root = data_dir.resolve()
    resolved = (root / relative).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("audio_ref.clean_flac escapes DATA_DIR")
    if resolved.suffix.lower() != ".flac":
        raise ValueError(f"clean audio must use .flac: {relative}")
    if not resolved.is_file():
        raise ValueError(f"clean FLAC does not exist: {relative}")
    with resolved.open("rb") as handle:
        if handle.read(4) != b"fLaC":
            raise ValueError(f"clean audio lacks FLAC signature: {relative}")
    return resolved


def load_transcripts(path: Path | None) -> dict[str, str]:
    """Load an optional transcript sidecar keyed by utterance identifier."""
    LOGGER.info("load_transcripts called provided=%s", path is not None)
    if path is None:
        return {}
    transcripts: dict[str, str] = {}
    for row in read_jsonl([path]):
        utterance_id = row.get("utterance_id")
        transcript = row.get("transcript")
        if not isinstance(utterance_id, str) or not isinstance(transcript, str):
            raise ValueError("transcript rows require string utterance_id and transcript")
        if utterance_id in transcripts:
            raise ValueError(f"duplicate transcript for utterance_id={utterance_id}")
        transcripts[utterance_id] = transcript.strip()
    return transcripts


def build_sft_row(
    record: dict[str, Any],
    audio_path: Path,
    mode: str,
    transcripts: dict[str, str],
) -> dict[str, Any]:
    """Convert one eligible golden record into an audio or text conversation."""
    utterance_id = record.get("utterance_id")
    LOGGER.info("build_sft_row called utterance_id=%s mode=%s", utterance_id, mode)
    native_language = record.get("native_lang_tag")
    target = record.get("common_lang_text")
    if not all(isinstance(value, str) and value.strip() for value in (utterance_id, native_language, target)):
        raise ValueError("eligible records require utterance_id, native_lang_tag, common_lang_text")
    if mode == "audio":
        user_content: list[dict[str, str]] = [
            {"type": "audio", "audio": str(audio_path)},
            {"type": "text", "text": TASK_TEMPLATE.format(native_language=native_language)},
        ]
    elif mode == "text":
        transcript = transcripts.get(utterance_id)
        if not transcript:
            raise ValueError(
                f"text fallback requires a non-empty transcript sidecar row for {utterance_id}"
            )
        user_content = [
            {
                "type": "text",
                "text": TEXT_TASK_TEMPLATE.format(
                    native_language=native_language,
                    transcript=transcript,
                ),
            }
        ]
    else:
        raise ValueError("mode must be 'audio' or 'text'")
    return {
        "utterance_id": utterance_id,
        "native_lang_tag": native_language,
        "target": target,
        "input_mode": mode,
        "messages": [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": [{"type": "text", "text": target}]},
        ],
    }


def deterministic_stratified_split(
    rows: list[dict[str, Any]],
    config: TuneConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split rows deterministically with native-language largest-remainder quotas."""
    LOGGER.info(
        "deterministic_stratified_split called row_count=%d fraction=%s seed=%d",
        len(rows),
        config.holdout_fraction,
        config.split_seed,
    )
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["native_lang_tag"]].append(row)
    target_count = int(len(rows) * config.holdout_fraction + 0.5)
    exact = {language: len(group) * config.holdout_fraction for language, group in groups.items()}
    quotas = {language: int(value) for language, value in exact.items()}
    remaining = target_count - sum(quotas.values())
    ranked_languages = sorted(
        groups,
        key=lambda language: (
            -(exact[language] - quotas[language]),
            hashlib.sha256(f"{config.split_seed}:{language}".encode()).hexdigest(),
        ),
    )
    for language in ranked_languages[:remaining]:
        quotas[language] += 1

    train: list[dict[str, Any]] = []
    holdout: list[dict[str, Any]] = []
    for language, group in sorted(groups.items()):
        ordered = sorted(
            group,
            key=lambda row: hashlib.sha256(
                f"{config.split_seed}:{row['utterance_id']}".encode()
            ).hexdigest(),
        )
        holdout.extend(ordered[: quotas[language]])
        train.extend(ordered[quotas[language] :])
    return train, holdout


def validate_and_prepare(
    corpus_path: Path,
    data_dir: Path,
    mode: str,
    transcripts_path: Path | None,
    config: TuneConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Validate source records and return prepared train/holdout rows in memory."""
    LOGGER.info(
        "validate_and_prepare called corpus_name=%s data_dir_name=%s mode=%s",
        corpus_path.name,
        data_dir.name,
        mode,
    )
    source_rows = read_jsonl(discover_shards(corpus_path))
    transcripts = load_transcripts(transcripts_path)
    prepared = [
        build_sft_row(record, resolve_audio_path(record, data_dir), mode, transcripts)
        for record in source_rows
        if is_training_eligible(record)
    ]
    if not prepared:
        raise ValueError("corpus contains no training-eligible records")
    train, holdout = deterministic_stratified_split(prepared, config)
    return train, holdout, len(source_rows)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    """Write rows atomically enough for an isolated caller-selected output directory."""
    rows = list(rows)
    LOGGER.info("write_jsonl called output_name=%s row_count=%d", path.name, len(rows))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def build_parser() -> argparse.ArgumentParser:
    """Create the preparation CLI parser without performing filesystem work."""
    LOGGER.info("build_parser called")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mode", choices=("audio", "text"), default="audio")
    parser.add_argument("--transcripts", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run validation and optionally emit deterministic train and holdout JSONL."""
    LOGGER.info("main called argv_provided=%s", argv is not None)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    if not args.dry_run and args.output is None:
        raise SystemExit("--output is required unless --dry-run is used")
    config = load_config()
    train, holdout, source_count = validate_and_prepare(
        args.corpus,
        args.data_dir,
        args.mode,
        args.transcripts,
        config,
    )
    if args.dry_run:
        print(
            f"DRY RUN OK: source={source_count} eligible={len(train) + len(holdout)} "
            f"train={len(train)} holdout={len(holdout)} mode={args.mode}"
        )
        return 0
    train_path = args.output / "train.jsonl"
    holdout_path = args.output / "holdout.jsonl"
    write_jsonl(train_path, train)
    write_jsonl(holdout_path, holdout)
    from tune.manifest import build_dataset_manifest, write_manifest

    manifest = build_dataset_manifest(
        discover_shards(args.corpus),
        train_path,
        holdout_path,
        config,
    )
    write_manifest(args.output / "dataset_manifest.json", manifest)
    print(f"Prepared train={len(train)} holdout={len(holdout)} mode={args.mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

