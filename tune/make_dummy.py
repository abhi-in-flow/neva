"""Generate a deterministic 100-row synthetic corpus in a caller-owned directory.

The command never writes under the real runtime ``data`` tree implicitly. It
creates canonical-looking golden JSONL, transcript sidecars, and tiny FLAC
signature fixtures suitable for preparation and dry-run validation only. The
audio fixtures are intentionally not playable and must never be used to claim
that model training or audio decoding succeeded.
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tune.config import load_config

LOGGER = logging.getLogger(__name__)
LANGUAGES = ("as-IN", "bn-IN", "bho-IN", "ne-IN", "or-IN")
TARGETS = ("water pot", "fish trap", "tea cup", "jackfruit", "bicycle")
TRANSCRIPTS = (
    "এইটো পানী ৰখা পাত্ৰ",
    "এটা মাছ ধরার ফাঁদ",
    "ई चाय पिए के कप ह",
    "यो रुखमा फलेको फल हो",
    "ଏହା ଦୁଇ ଚକିଆ ଯାନ",
)
BASE_TIMESTAMP = datetime(2026, 7, 11, 6, 0, tzinfo=UTC)


def build_record(index: int) -> tuple[dict[str, object], dict[str, str]]:
    """Build one deterministic eligible golden record and transcript sidecar row."""
    LOGGER.info("build_record called index=%d", index)
    utterance_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"dialect-dummy-utterance-{index}"))
    image_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"dialect-dummy-image-{index % 5}"))
    deck_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "dialect-dummy-deck"))
    player_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"dialect-dummy-player-{index % 10}"))
    session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"dialect-dummy-session-{index // 10}"))
    timestamp = (BASE_TIMESTAMP + timedelta(seconds=index)).isoformat()
    language_index = index % len(LANGUAGES)
    record: dict[str, object] = {
        "utterance_id": utterance_id,
        "audio_ref": {
            "raw_webm": f"audio/{utterance_id}.webm",
            "clean_flac": f"audio/{utterance_id}.flac",
        },
        "native_lang_tag": LANGUAGES[language_index],
        "common_lang_text": TARGETS[language_index],
        "image_id": image_id,
        "deck_id": deck_id,
        "validation": {
            "guesser_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"dummy-guesser-{index}")),
            "correct": True,
            "attempts": 0,
        },
        "quality": {
            "is_speech": True,
            "single_speaker": True,
            "audio_quality_ok": True,
            "duration_s": 3.0,
            "dedup_hash": hashlib_for_index(index),
            "duplicate": False,
            "contamination_flag": False,
            "apparent_language_note": "synthetic fixture",
        },
        "speaker_meta": {
            "player_id": player_id,
            "declared_region": None,
            "session_id": session_id,
        },
        "timestamps": {"captured_at": timestamp, "packaged_at": timestamp},
        "training_eligible": True,
        "synthetic_fixture": True,
    }
    transcript = {"utterance_id": utterance_id, "transcript": TRANSCRIPTS[language_index]}
    return record, transcript


def hashlib_for_index(index: int) -> str:
    """Return a stable non-sensitive fixture fingerprint for an integer row."""
    LOGGER.info("hashlib_for_index called index=%d", index)
    return uuid.uuid5(uuid.NAMESPACE_OID, f"dummy-audio-{index}").hex


def validate_output(output: Path, dry_run: bool) -> None:
    """Ensure generation targets a new or empty caller-selected directory."""
    LOGGER.info("validate_output called output_name=%s dry_run=%s", output.name, dry_run)
    if output.exists() and not output.is_dir():
        raise ValueError("dummy output must be a directory")
    if output.exists() and any(output.iterdir()):
        raise ValueError("dummy output directory must be empty")


def generate_dummy(output: Path, rows: int) -> None:
    """Write deterministic synthetic records, transcript rows, and FLAC stubs."""
    LOGGER.info("generate_dummy called output_name=%s rows=%d", output.name, rows)
    corpus_dir = output / "corpus"
    audio_dir = output / "audio"
    corpus_dir.mkdir(parents=True, exist_ok=False)
    audio_dir.mkdir(parents=True, exist_ok=False)
    shard_path = corpus_dir / "shard_0001.jsonl"
    transcript_path = output / "transcripts.jsonl"
    with (
        shard_path.open("w", encoding="utf-8", newline="\n") as shard,
        transcript_path.open("w", encoding="utf-8", newline="\n") as sidecar,
    ):
        for index in range(rows):
            record, transcript = build_record(index)
            shard.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            sidecar.write(json.dumps(transcript, ensure_ascii=False, sort_keys=True) + "\n")
            utterance_id = record["utterance_id"]
            (audio_dir / f"{utterance_id}.flac").write_bytes(b"fLaC\x00dummy-fixture")


def build_parser() -> argparse.ArgumentParser:
    """Create the dummy-corpus command-line parser."""
    LOGGER.info("build_parser called")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=int)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate or generate a synthetic corpus without touching runtime data."""
    LOGGER.info("main called argv_provided=%s", argv is not None)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    config = load_config()
    rows = args.rows if args.rows is not None else config.dummy_rows
    if rows <= 0:
        raise SystemExit("--rows must be positive")
    validate_output(args.output, args.dry_run)
    if args.dry_run:
        print(f"DRY RUN OK: would create rows={rows} under caller-selected output")
        return 0
    generate_dummy(args.output, rows)
    print(f"Created synthetic fixture rows={rows} at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

