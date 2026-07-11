"""Focused fixture tests for the cleaning gauntlet's pure quality gates.

Tests use fake structured GenAI responses and a temporary corpus directory.
They never construct an asyncpg pool, invoke ffmpeg, call Gemini, or use the
live ``data/`` directory.
"""

from __future__ import annotations

import json

import pytest

from worker.corpus import CorpusWriter
from worker.models import TurnContext
from worker.service import _golden_record, _parse_triage, _training_eligible


def _context(*, outcome: str, quality: dict[str, object] | None) -> TurnContext:
    """Build an isolated, contract-shaped turn snapshot for fixture testing."""
    return TurnContext(
        turn_id="00000000-0000-0000-0000-000000000001",
        pair_id="00000000-0000-0000-0000-000000000002",
        speaker_id="00000000-0000-0000-0000-000000000003",
        guesser_id="00000000-0000-0000-0000-000000000004",
        native_lang="as",
        common_langs=["en", "hi"],
        common_lang="hi",
        card_id="00000000-0000-0000-0000-000000000005",
        deck_id="00000000-0000-0000-0000-000000000006",
        label_common={"en": "water pot", "hi": "घड़ा"},
        audio_path="audio/00000000-0000-0000-0000-000000000001.webm",
        audio_flac_path="audio/00000000-0000-0000-0000-000000000001.flac",
        duration_s=3.2,
        quality=quality,
        status="scored",
        outcome=outcome,
        attempts=0,
        captured_at="2026-07-11T06:00:00+00:00",
    )


@pytest.fixture
def clean_response() -> dict[str, object]:
    """Provide an accepted fake Gemini structured response."""
    return {
        "is_speech": True,
        "single_speaker": True,
        "audio_quality_ok": True,
        "is_label_readout": False,
        "readout_reasoning": "Descriptive utterance.",
        "apparent_language_note": "Assamese-like speech.",
        "duration_estimate_s": 3.2,
        "confidence": 0.94,
    }


def test_clean_record_is_eligible_and_appended(
    tmp_path, clean_response: dict[str, object]
) -> None:
    """Accept a clean validated fixture and append exactly one canonical line."""
    quality = _parse_triage(clean_response, "a" * 64, False).as_quality_json()
    context = _context(outcome="validated", quality=quality)
    assert _training_eligible(context) is True

    golden = _golden_record(context)
    assert golden["common_lang_text"] == "घड़ा"
    shard = CorpusWriter(tmp_path / "corpus", shard_record_limit=2).append(golden)
    assert shard == "shard_0001.jsonl"
    assert json.loads((tmp_path / "corpus" / shard).read_text(encoding="utf-8"))["utterance_id"] == context.turn_id


def test_silent_fixture_is_not_eligible(clean_response: dict[str, object]) -> None:
    """Reject a fake silent recording even when human scoring was validated."""
    clean_response["is_speech"] = False
    quality = _parse_triage(clean_response, "b" * 64, False).as_quality_json()
    assert _training_eligible(_context(outcome="validated", quality=quality)) is False


def test_label_readout_fixture_is_contaminated(clean_response: dict[str, object]) -> None:
    """Reject a fake bare-label utterance through contamination gating."""
    clean_response["is_label_readout"] = True
    clean_response["readout_reasoning"] = "Only the Hindi label was said."
    quality = _parse_triage(clean_response, "c" * 64, False).as_quality_json()
    context = _context(outcome="validated", quality=quality)
    assert context.quality is not None
    assert context.quality["contamination_flag"] is True
    assert _training_eligible(context) is False


def test_corpus_rotation_uses_next_shard(tmp_path) -> None:
    """Rotate append-only shards precisely at the configured record limit."""
    writer = CorpusWriter(tmp_path / "corpus", shard_record_limit=1)
    assert writer.append({"utterance_id": "one"}) == "shard_0001.jsonl"
    assert writer.append({"utterance_id": "two"}) == "shard_0002.jsonl"
