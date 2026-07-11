"""Focused tests for operator-controlled deck generation and publication.

The suite validates strict operator JSON conversion, explicit concept ordering,
per-concept locale overrides, provided deck identifiers, ready publication,
failure transitions, and CLI card-count derivation. All GenAI behavior is fake
and all database interactions are mocked; no runtime data or services mutate.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from deckgen.cli import main
from deckgen.client import FakeDeckGenAIClient, GeneratedImage
from deckgen.concepts import CONCEPTS, concepts_from_operator_mappings
from deckgen.config import FAKE_IMAGE_BYTES
from deckgen.pipeline import build_deck
from deckgen.publish import CardRecord, InMemoryPublisher, PostgresPublisher

logger = logging.getLogger(__name__)


def _operator_rows(count: int = 6) -> list[dict[str, str]]:
    """Build a valid operator concept payload of the requested size."""
    logger.info("_operator_rows called count=%s", count)
    return [
        {
            "concept_id": f"operator_{index}",
            "label_en": f"operator label {index}",
            "locale": f"Operator locale {index}",
            "cultural_hint": f"a locally recognizable object number {index}",
        }
        for index in range(count)
    ]


class PromptRecordingClient(FakeDeckGenAIClient):
    """Fake client that retains text prompts for locale assertions."""

    def __init__(self) -> None:
        """Initialize fake behavior and empty safe prompt history."""
        super().__init__()
        self.image_prompts: list[str] = []
        self.verify_prompts: list[str] = []

    async def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        operation: str,
    ) -> GeneratedImage:
        """Record an image prompt, then return the deterministic fake image."""
        self.image_prompts.append(prompt)
        return await super().generate_image(
            model=model,
            prompt=prompt,
            operation=operation,
        )

    async def generate_json(
        self,
        *,
        model: str,
        prompt: str,
        operation: str,
        response_schema: dict[str, Any],
        thinking_level: str | None = None,
        image_bytes: bytes | None = None,
        image_mime_type: str | None = None,
    ) -> Any:
        """Record verification prompts and delegate deterministic JSON output."""
        if operation == "verify_image":
            self.verify_prompts.append(prompt)
        return await super().generate_json(
            model=model,
            prompt=prompt,
            operation=operation,
            response_schema=response_schema,
            thinking_level=thinking_level,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
        )


def test_operator_conversion_maps_metadata_and_preserves_curated_pool() -> None:
    """Operator rows map fields exactly without altering curated concepts."""
    logger.info("test_operator_conversion_maps_metadata_and_preserves_curated_pool")
    curated_snapshot = tuple(CONCEPTS)
    concepts = concepts_from_operator_mappings(_operator_rows())
    first = concepts[0]
    assert first.id == "operator_0"
    assert first.labels == {"en": "operator label 0"}
    assert first.concept_noun == "operator label 0"
    assert first.concept_phrase == "a locally recognizable object number 0"
    assert first.cultural_hint == first.concept_phrase
    assert first.locale == "Operator locale 0"
    assert CONCEPTS == curated_snapshot
    assert all(concept.locale is None for concept in CONCEPTS)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda rows: rows + [dict(rows[0])], "duplicate"),
        (
            lambda rows: [dict(rows[0], label_en="   "), *rows[1:]],
            "must not be blank",
        ),
        (
            lambda rows: [dict(rows[0], concept_id="invalid id!"), *rows[1:]],
            "invalid concept_id",
        ),
    ],
)
def test_operator_conversion_rejects_invalid_rows(mutator: Any, message: str) -> None:
    """Duplicate ids, blank fields, and malformed ids are rejected."""
    logger.info("test_operator_conversion_rejects_invalid_rows message=%s", message)
    with pytest.raises(ValueError, match=message):
        concepts_from_operator_mappings(mutator(_operator_rows()))


@pytest.mark.asyncio
async def test_explicit_concepts_use_per_concept_locale_and_ready_status() -> None:
    """Explicit concepts bypass selection and override deck region per card."""
    logger.info("test_explicit_concepts_use_per_concept_locale_and_ready_status")
    concepts = concepts_from_operator_mappings(_operator_rows())
    client = PromptRecordingClient()
    publisher = InMemoryPublisher()
    provided_deck_id = uuid.uuid4()

    result = await build_deck(
        region="assam",
        concepts=concepts,
        dry_run=True,
        client=client,
        publisher=publisher,
        deck_id=provided_deck_id,
        final_status="ready",
    )

    assert [card.concept.id for card in result.cards] == [concept.id for concept in concepts]
    assert result.publish is not None
    assert result.publish.deck_id == provided_deck_id
    assert result.publish.status == "ready"
    assert provided_deck_id in publisher.ready_decks
    assert provided_deck_id not in publisher.live_decks
    assert all(
        concept.locale in prompt
        for concept, prompt in zip(concepts, client.image_prompts, strict=True)
    )
    assert all(
        concept.locale in prompt
        for concept, prompt in zip(concepts, client.verify_prompts, strict=True)
    )
    intent = publisher.published[0]
    assert intent["provided_deck_id"] is True
    assert intent["generation_input"]["source"] == "operator"
    assert intent["generation_metrics"]["images_accepted"] == 6
    assert set(intent["concept_ids"].values()) == {concept.id for concept in concepts}


@pytest.mark.asyncio
async def test_generation_failure_marks_provided_deck_failed_only() -> None:
    """A failed custom generation cannot finalize a provided deck."""
    logger.info("test_generation_failure_marks_provided_deck_failed_only")
    concepts = concepts_from_operator_mappings(_operator_rows())
    failures = [
        {
            "depicts_label": False,
            "has_text": False,
            "cultural_ok": True,
            "verdict": "fail",
            "reason": "ambiguous",
        }
        for _ in range(3)
    ]
    publisher = InMemoryPublisher()
    deck_id = uuid.uuid4()
    with pytest.raises(RuntimeError, match="verification failed"):
        await build_deck(
            region="assam",
            concepts=concepts,
            client=FakeDeckGenAIClient(verify_results=failures),
            publisher=publisher,
            deck_id=deck_id,
            final_status="ready",
        )
    assert publisher.failed_decks == [deck_id]
    assert publisher.ready_decks == []
    assert publisher.live_decks == []


@pytest.mark.asyncio
async def test_postgres_provided_deck_is_not_reinserted_and_stores_concept(
    tmp_path: Path,
) -> None:
    """Provided generating decks are locked, updated, and never inserted."""
    logger.info("test_postgres_provided_deck_is_not_reinserted_and_stores_concept")
    deck_id = uuid.uuid4()
    card_id = uuid.uuid4()
    publisher = PostgresPublisher(
        database_url="postgresql://u:p@localhost:5432/db",
        data_dir=tmp_path / "data",
    )
    card = CardRecord(
        card_id=card_id,
        concept_id="operator_0",
        image_bytes=FAKE_IMAGE_BYTES,
        label_common={"en": "operator label 0"},
    )
    connection = AsyncMock()
    transaction = AsyncMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=None)
    connection.transaction = lambda: transaction
    connection.fetchrow = AsyncMock(return_value={"status": "generating"})
    connection.execute = AsyncMock()
    connection.close = AsyncMock()

    with patch(
        "deckgen.publish.asyncpg.connect",
        new=AsyncMock(return_value=connection),
    ):
        result = await publisher.publish(
            region_tag="assam",
            cards=[card],
            deck_id=deck_id,
            final_status="ready",
            generation_input={"source": "operator"},
            generation_metrics={"images_accepted": 1},
        )

    assert result.deck_id == deck_id
    assert result.status == "ready"
    sql_calls = connection.execute.await_args_list
    assert not any("INSERT INTO decks" in call.args[0] for call in sql_calls)
    card_insert = next(call for call in sql_calls if "INSERT INTO cards" in call.args[0])
    assert card_insert.args[3] == "operator_0"


def test_cli_concepts_file_derives_count_and_rejects_mismatch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI derives custom card count unless an explicit count disagrees."""
    logger.info("test_cli_concepts_file_derives_count_and_rejects_mismatch")
    path = tmp_path / "concepts.json"
    path.write_text(
        json.dumps({"concepts": _operator_rows()}),
        encoding="utf-8",
    )
    assert main(["--concepts-file", str(path), "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["card_count"] == 6
    assert payload["status"] == "live"

    assert (
        main(
            [
                "--concepts-file",
                str(path),
                "--cards",
                "7",
                "--dry-run",
            ]
        )
        == 2
    )
    assert "must match" in capsys.readouterr().err
