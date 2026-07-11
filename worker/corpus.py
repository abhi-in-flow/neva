"""Append-only JSONL shard writer owned exclusively by the gauntlet.

The writer chooses the current numbered shard, appends one canonical JSON line,
fsyncs the file, and rotates when the configured record count is reached. It
never rewrites existing corpus content or accepts client-provided paths.

Crash recovery depends on ``find_utterance``: after a process dies between
append and ``records.shard_file`` linkage, a retry locates the existing line
instead of duplicating it. Concurrent workers must hold the repository
advisory flusher lock around append and link operations.
"""

from __future__ import annotations

import json
import logging
import os
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

    def find_utterance(self, utterance_id: str) -> str | None:
        """Locate an existing shard line for an utterance after a crash window.

        Args:
            utterance_id: Canonical turn UUID previously appended.

        Returns:
            Shard filename containing the utterance, or ``None`` if absent.
        """
        logger.info("CorpusWriter.find_utterance called utterance_id=%s", utterance_id)
        if not self._corpus_dir.is_dir():
            return None
        for shard in sorted(self._corpus_dir.glob("shard_*.jsonl")):
            if self._shard_contains(shard, utterance_id):
                logger.info(
                    "CorpusWriter.find_utterance found utterance_id=%s shard=%s",
                    utterance_id,
                    shard.name,
                )
                return shard.name
        return None

    def append(self, golden: dict[str, object]) -> str:
        """Append one eligible canonical record and return its relative shard name.

        Args:
            golden: Canonical JSON-compatible record. Must include ``utterance_id``.

        Returns:
            Filename of the shard that received the line.

        Raises:
            ValueError: If ``utterance_id`` is missing.
        """
        utterance_id = golden.get("utterance_id")
        logger.info("CorpusWriter.append called utterance_id=%s", utterance_id)
        if not isinstance(utterance_id, str) or not utterance_id:
            raise ValueError("golden record requires utterance_id")
        existing = self.find_utterance(utterance_id)
        if existing is not None:
            logger.info(
                "CorpusWriter.append idempotent utterance_id=%s shard=%s",
                utterance_id,
                existing,
            )
            return existing
        self._corpus_dir.mkdir(parents=True, exist_ok=True)
        shard = self._current_shard()
        encoded = json.dumps(golden, ensure_ascii=False, separators=(",", ":"))
        with shard.open("a", encoding="utf-8", newline="\n") as output:
            output.write(encoded)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
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

    @staticmethod
    def _shard_contains(path: Path, utterance_id: str) -> bool:
        """Return whether any JSONL line in ``path`` carries ``utterance_id``.

        Args:
            path: Existing shard file.
            utterance_id: Turn UUID to locate.

        Returns:
            ``True`` when a parseable line matches the utterance.
        """
        logger.info(
            "CorpusWriter._shard_contains called shard=%s utterance_id=%s",
            path.name,
            utterance_id,
        )
        with path.open("r", encoding="utf-8") as input_file:
            for line in input_file:
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if payload.get("utterance_id") == utterance_id:
                    return True
        return False
