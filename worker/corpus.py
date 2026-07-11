"""Append-only JSONL shard writer owned exclusively by the gauntlet.

The writer chooses the current numbered shard, appends one canonical JSON line,
and rotates when the configured record count is reached. It never rewrites
existing corpus content or accepts client-provided paths.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class CorpusWriter:
    """Write eligible golden records into contract-defined JSONL shards."""

    def __init__(self, corpus_dir: Path, shard_record_limit: int) -> None:
        """Initialize the append-only corpus destination.

        Args:
            corpus_dir: Isolated or live ``DATA_DIR/corpus`` path.
            shard_record_limit: Maximum lines in a shard before rotation.
        """
        self._corpus_dir = corpus_dir
        self._shard_record_limit = shard_record_limit
        logger.info(
            "CorpusWriter initialized corpus_dir=%s shard_record_limit=%s",
            corpus_dir,
            shard_record_limit,
        )

    def append(self, golden: dict[str, object]) -> str:
        """Append one eligible canonical record and return its relative shard name.

        Args:
            golden: Canonical JSON-compatible record.

        Returns:
            Filename of the shard that received the line.
        """
        logger.info("CorpusWriter.append called utterance_id=%s", golden.get("utterance_id"))
        self._corpus_dir.mkdir(parents=True, exist_ok=True)
        shard = self._current_shard()
        encoded = json.dumps(golden, ensure_ascii=False, separators=(",", ":"))
        with shard.open("a", encoding="utf-8", newline="\n") as output:
            output.write(encoded)
            output.write("\n")
            output.flush()
        logger.info("CorpusWriter.append completed shard=%s line_bytes=%s", shard.name, len(encoded))
        return shard.name

    def _current_shard(self) -> Path:
        """Return a non-full shard, allocating the first or next shard as needed."""
        logger.info("CorpusWriter._current_shard called corpus_dir=%s", self._corpus_dir)
        shard_numbers = sorted(
            int(path.stem.removeprefix("shard_"))
            for path in self._corpus_dir.glob("shard_*.jsonl")
            if path.stem.removeprefix("shard_").isdigit()
        )
        number = shard_numbers[-1] if shard_numbers else 1
        candidate = self._corpus_dir / f"shard_{number:04d}.jsonl"
        if candidate.exists() and self._line_count(candidate) >= self._shard_record_limit:
            candidate = self._corpus_dir / f"shard_{number + 1:04d}.jsonl"
        return candidate

    @staticmethod
    def _line_count(path: Path) -> int:
        """Count prior JSONL records without parsing their payloads."""
        logger.info("CorpusWriter._line_count called shard=%s", path.name)
        with path.open("r", encoding="utf-8") as input_file:
            return sum(1 for _ in input_file)
