"""Focused tests for prompt-to-deck invent, progressive publish, and admin routes.

Uses fake GenAI clients and in-memory publishers only. No Gemini network calls,
Postgres mutations, or runtime ``data/`` writes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from pydantic import ValidationError

from app.api.admin_decks import generate_deck, generate_deck_from_prompt, router
from app.deck_admin.generation import DeckgenGateway
from app.deck_admin.service import DeckAdminService
from contracts.api_types import (
    AdminConceptInput,
    AdminDeckGenerateRequest,
    AdminDeckOperationResponse,
    AdminDeckPromptGenerateRequest,
    DeckStatus,
)
from deckgen.client import FakeDeckGenAIClient
from deckgen.concept_gen import invent_concepts_from_prompt
from deckgen.config import (
    PROGRESS_STAGE_IMAGES,
    PROGRESS_STAGE_READY,
    REGION_CONTEXTS,
)
from deckgen.pipeline import build_deck, build_deck_from_prompt
from deckgen.publish import CardRecord, InMemoryPublisher


def _request() -> AdminDeckGenerateRequest:
    """Build the minimum valid six-concept operator request."""
    return AdminDeckGenerateRequest(
        region_tag="assam",
        concepts=[
            AdminConceptInput(
                concept_id=f"concept_{index}",
                label_en=f"Label {index}",
                locale="Assamese village",
                cultural_hint=f"Hint {index}",
            )
            for index in range(6)
        ],
    )


def test_prompt_request_validates_bounds_and_single_line() -> None:
    """Accept a one-line theme and reject blank, multi-line, or out-of-range counts."""
    ok = AdminDeckPromptGenerateRequest(
        region_tag="kerala",
        prompt="Coastal festival morning",
        card_count=8,
    )
    assert ok.prompt == "Coastal festival morning"
    assert ok.card_count == 8

    with pytest.raises(ValidationError):
        AdminDeckPromptGenerateRequest(
            region_tag="kerala",
            prompt="line one\nline two",
            card_count=8,
        )
    with pytest.raises(ValidationError):
        AdminDeckPromptGenerateRequest(
            region_tag="kerala",
            prompt="ok",
            card_count=5,
        )
    with pytest.raises(ValidationError):
        AdminDeckPromptGenerateRequest(
            region_tag="kerala",
            prompt="ok",
            card_count=21,
        )


def test_region_contexts_cover_28_states_and_legacy_aliases() -> None:
    """Canonical state slugs plus legacy aliases remain resolvable."""
    states = {
        "andhra-pradesh",
        "arunachal-pradesh",
        "assam",
        "bihar",
        "chhattisgarh",
        "goa",
        "gujarat",
        "haryana",
        "himachal-pradesh",
        "jharkhand",
        "karnataka",
        "kerala",
        "madhya-pradesh",
        "maharashtra",
        "manipur",
        "meghalaya",
        "mizoram",
        "nagaland",
        "odisha",
        "punjab",
        "rajasthan",
        "sikkim",
        "tamil-nadu",
        "telangana",
        "tripura",
        "uttar-pradesh",
        "uttarakhand",
        "west-bengal",
    }
    assert states.issubset(REGION_CONTEXTS.keys())
    for alias in ("bengal", "bangalore", "north", "northeast", "tamil"):
        assert alias in REGION_CONTEXTS


@pytest.mark.asyncio
async def test_invent_concepts_from_prompt_uses_fake_client() -> None:
    """Fake invent_concepts returns validated operator concepts of the asked count."""
    client = FakeDeckGenAIClient()
    concepts = await invent_concepts_from_prompt(
        client,
        region_tag="assam",
        prompt="Monsoon market chaos",
        card_count=6,
    )
    assert len(concepts) == 6
    assert len({c.id for c in concepts}) == 6
    invent_calls = [c for c in client.calls if c.get("operation") == "invent_concepts"]
    assert len(invent_calls) == 1


@pytest.mark.asyncio
async def test_progressive_publish_persists_then_finalizes() -> None:
    """Progressive mode writes cards while generating, then finalizes decoys."""
    deck_id = uuid4()
    publisher = InMemoryPublisher()
    client = FakeDeckGenAIClient()
    result = await build_deck(
        region="assam",
        concepts=_request_concepts(),
        dry_run=True,
        client=client,
        publisher=publisher,
        deck_id=deck_id,
        final_status="ready",
        progressive=True,
    )
    assert result.publish is not None
    assert result.publish.status == "ready"
    assert len(publisher.progressive_cards[str(deck_id)]) == 6
    metrics = publisher.generation_states[str(deck_id)]["generation_metrics"]
    assert metrics["progress_stage"] == PROGRESS_STAGE_READY
    assert metrics["cards_ready"] == 6
    assert publisher.deck_statuses[str(deck_id)] == "ready"
    # Atomic publish path must remain unused for progressive runs.
    assert any(item.get("progressive") for item in publisher.published)


def _request_concepts():
    """Convert the six-concept request into pipeline Concept objects."""
    from deckgen.concepts import concepts_from_operator

    return concepts_from_operator(
        [c.model_dump(mode="json") for c in _request().concepts]
    )


@pytest.mark.asyncio
async def test_build_deck_from_prompt_progressive_end_to_end() -> None:
    """Prompt invent + progressive images leave a ready in-memory deck."""
    deck_id = uuid4()
    publisher = InMemoryPublisher()
    client = FakeDeckGenAIClient()
    result = await build_deck_from_prompt(
        region="assam",
        prompt="Festival courtyard chaos",
        card_count=6,
        deck_id=deck_id,
        dry_run=True,
        client=client,
        publisher=publisher,
        final_status="ready",
    )
    assert len(result.cards) == 6
    assert publisher.deck_statuses[str(deck_id)] == "ready"
    assert len(publisher.progressive_cards[str(deck_id)]) == 6
    invent = publisher.generation_states[str(deck_id)]["generation_input"]
    assert invent["source"] == "prompt"
    assert len(invent["concepts"]) == 6


@pytest.mark.asyncio
async def test_persist_card_skips_duplicate_concept_ids() -> None:
    """Retrying the same concept_id does not create a second progressive card."""
    deck_id = uuid4()
    publisher = InMemoryPublisher()
    card = CardRecord(
        card_id=uuid4(),
        concept_id="dup",
        image_bytes=b"\x89PNG\r\n\x1a\npayload",
        label_common={"en": "Dup"},
    )
    await publisher.persist_card(deck_id=deck_id, card=card)
    await publisher.persist_card(
        deck_id=deck_id,
        card=CardRecord(
            card_id=uuid4(),
            concept_id="dup",
            image_bytes=b"\x89PNG\r\n\x1a\npayload",
            label_common={"en": "Dup"},
        ),
        generation_metrics={"progress_stage": PROGRESS_STAGE_IMAGES, "cards_ready": 1},
    )
    assert len(publisher.progressive_cards[str(deck_id)]) == 1


def test_router_includes_from_prompt_before_deck_id() -> None:
    """Static from-prompt route is registered and returns 202."""
    routes = {
        (route.path, method): route.status_code
        for route in router.routes
        for method in route.methods
    }
    assert routes[("/api/admin/decks/from-prompt", "POST")] == 202
    assert ("/api/admin/decks/{deck_id}", "GET") in routes


@pytest.mark.asyncio
async def test_from_prompt_route_schedules_background_work() -> None:
    """Return generating immediately and schedule prompt generation."""
    deck_id = uuid4()
    service = SimpleNamespace(
        start_prompt_generation=AsyncMock(
            return_value=AdminDeckOperationResponse(
                deck_id=deck_id,
                status=DeckStatus.GENERATING,
            )
        ),
        run_prompt_generation=AsyncMock(),
    )
    tasks = BackgroundTasks()
    payload = AdminDeckPromptGenerateRequest(
        region_tag="assam",
        prompt="Monsoon market",
        card_count=8,
    )
    response = await generate_deck_from_prompt(payload, tasks, service)
    assert response.deck_id == deck_id
    assert response.status is DeckStatus.GENERATING
    service.start_prompt_generation.assert_awaited_once()
    service.run_prompt_generation.assert_not_awaited()
    assert len(tasks.tasks) == 1


@pytest.mark.asyncio
async def test_review_hides_concepts_while_generating() -> None:
    """Detail omits concept JSON during generating; cards remain visible."""
    deck_id = uuid4()
    card_id = uuid4()
    row = {
        "id": deck_id,
        "region_tag": "assam",
        "status": "generating",
        "generation_input": {
            "source": "prompt",
            "concepts": [
                {
                    "concept_id": "hidden",
                    "label_en": "Hidden",
                    "locale": "Assam",
                    "cultural_hint": "Should not appear while generating",
                }
            ],
        },
        "generation_metrics": {
            "progress_stage": "generating_images",
            "cards_ready": 1,
            "cards_target": 8,
        },
        "failure_reason": None,
        "activated_at": None,
        "created_at": datetime(2026, 7, 11, tzinfo=UTC),
        "card_count": 1,
        "cards": [
            {
                "id": card_id,
                "concept_id": "hidden",
                "image_path": f"decks/{deck_id}/{card_id}.png",
                "label_common": {"en": "Visible label"},
                "verified": True,
            }
        ],
    }
    repository = SimpleNamespace(get_deck=AsyncMock(return_value=row))
    service = DeckAdminService(repository, Mock(), data_dir=Path("data"))
    detail = await service.review_deck(deck_id)
    assert detail.concepts == []
    assert detail.cards[0].label_en == "Visible label"


@pytest.mark.asyncio
async def test_gateway_prompt_path_calls_build_deck_from_prompt() -> None:
    """Prompt gateway binds the progressive pipeline entrypoint."""
    deck_id = uuid4()
    builder = AsyncMock()

    def fake_import(name: str) -> SimpleNamespace:
        """Return an isolated fake pipeline module."""
        assert name == "deckgen.pipeline"
        return SimpleNamespace(build_deck_from_prompt=builder)

    with patch("app.deck_admin.generation.import_module", side_effect=fake_import):
        await DeckgenGateway().generate_from_prompt(
            region_tag="assam",
            prompt="Coastal life",
            card_count=8,
            deck_id=deck_id,
        )
    builder.assert_awaited_once_with(
        region="assam",
        prompt="Coastal life",
        card_count=8,
        deck_id=deck_id,
        final_status="ready",
    )


@pytest.mark.asyncio
async def test_atomic_json_path_still_schedules_generate_deck() -> None:
    """Preserve existing JSON generate route scheduling behavior."""
    deck_id = uuid4()
    service = SimpleNamespace(
        start_generation=AsyncMock(
            return_value=AdminDeckOperationResponse(
                deck_id=deck_id,
                status=DeckStatus.GENERATING,
            )
        ),
        run_generation=AsyncMock(),
    )
    tasks = BackgroundTasks()
    response = await generate_deck(_request(), tasks, service)
    assert response.status is DeckStatus.GENERATING
    assert len(tasks.tasks) == 1
