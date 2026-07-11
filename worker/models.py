"""Typed worker-only values used at the database and GenAI boundaries.

These dataclasses make the worker independently testable: the database adapter
returns immutable turn snapshots and the small GenAI protocol accepts only a
FLAC path and typed prompt inputs, never raw audio payloads in logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Job:
    """A claimed durable job with its retry count."""

    id: str
    kind: str
    turn_id: str
    tries: int


@dataclass(frozen=True, slots=True)
class TurnContext:
    """All contract data required for triage or immutable record packaging."""

    turn_id: str
    pair_id: str
    speaker_id: str
    guesser_id: str
    native_lang: str
    common_langs: list[str]
    common_lang: str
    card_id: str
    deck_id: str
    label_common: dict[str, str]
    audio_path: str
    audio_flac_path: str | None
    duration_s: float | None
    quality: dict[str, Any] | None
    status: str
    outcome: str
    attempts: int
    captured_at: str


@dataclass(frozen=True, slots=True)
class TriageResult:
    """Validated machine-quality result persisted inside ``turns.quality``."""

    is_speech: bool
    single_speaker: bool
    audio_quality_ok: bool
    contamination_flag: bool
    apparent_language_note: str
    duration_s: float
    confidence: float
    dedup_hash: str
    duplicate: bool
    readout_reasoning: str

    def as_quality_json(self) -> dict[str, object]:
        """Serialize the result into the frozen quality JSON contract."""
        return {
            "is_speech": self.is_speech,
            "single_speaker": self.single_speaker,
            "audio_quality_ok": self.audio_quality_ok,
            "duration_s": self.duration_s,
            "dedup_hash": self.dedup_hash,
            "duplicate": self.duplicate,
            "contamination_flag": self.contamination_flag,
            "apparent_language_note": self.apparent_language_note,
            "confidence": self.confidence,
            "readout_reasoning": self.readout_reasoning,
        }


class TriageClient(Protocol):
    """Small worker-owned adapter expected from a shared GenAI client."""

    async def triage_audio(
        self,
        *,
        model: str,
        prompt: str,
        response_schema: dict[str, object],
        audio_path: Path,
        thinking_level: str,
    ) -> dict[str, object]:
        """Return schema-conforming triage JSON for the supplied FLAC file."""
