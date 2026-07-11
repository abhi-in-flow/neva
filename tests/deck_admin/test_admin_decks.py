"""Isolated tests for deck-admin auth, scheduling, review, and activation.

All collaborators are fakes or mocks. Tests do not open Postgres, call Gemini,
write runtime images, or mutate production/application data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.admin_decks import generate_deck, router
from app.config import Settings
from app.deck_admin.deps import require_deck_admin_key
from app.deck_admin.generation import DeckgenGateway
from app.deck_admin.repository import PostgresDeckAdminRepository
from app.deck_admin.service import DeckAdminError, DeckAdminService
from contracts.api_types import (
    AdminConceptInput,
    AdminDeckGenerateRequest,
    AdminDeckOperationResponse,
    DeckStatus,
)


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


def _deck_row(deck_id: UUID, *, status: str = "ready") -> dict[str, object]:
    """Build a repository-shaped deck row for service tests."""
    return {
        "id": deck_id,
        "region_tag": "assam",
        "status": status,
        "generation_input": _request().model_dump(mode="json"),
        "generation_metrics": {"images_per_minute": 12.5},
        "failure_reason": None,
        "activated_at": None,
        "created_at": datetime(2026, 7, 11, tzinfo=UTC),
        "card_count": 1,
        "cards": [],
    }


def test_router_wires_all_admin_paths_and_post_accepts_async_work() -> None:
    """Expose generation, list, review, and activation with the expected status."""
    routes = {
        (route.path, method): route.status_code
        for route in router.routes
        for method in route.methods
    }
    assert routes[("/api/admin/decks", "POST")] == 202
    assert routes[("/api/admin/decks/from-prompt", "POST")] == 202
    assert ("/api/admin/decks", "GET") in routes
    assert ("/api/admin/decks/{deck_id}", "GET") in routes
    assert ("/api/admin/decks/{deck_id}/activate", "POST") in routes


@pytest.mark.asyncio
async def test_auth_returns_503_when_key_unconfigured() -> None:
    """Reject all admin traffic when the server has no configured key."""
    with patch(
        "app.deck_admin.deps.get_settings",
        return_value=Settings(deck_admin_api_key=""),
    ):
        with pytest.raises(HTTPException) as caught:
            await require_deck_admin_key("anything")
    assert caught.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("supplied", [None, "wrong"])
async def test_auth_returns_401_for_absent_or_wrong_key(supplied: str | None) -> None:
    """Reject absent and incorrect headers without revealing key material."""
    with patch(
        "app.deck_admin.deps.get_settings",
        return_value=Settings(deck_admin_api_key="correct"),
    ):
        with pytest.raises(HTTPException) as caught:
            await require_deck_admin_key(supplied)
    assert caught.value.status_code == 401


@pytest.mark.asyncio
async def test_auth_accepts_matching_key_with_constant_time_primitive() -> None:
    """Authenticate the matching header through ``hmac.compare_digest``."""
    settings = Settings(deck_admin_api_key="correct")
    with (
        patch("app.deck_admin.deps.get_settings", return_value=settings),
        patch(
            "app.deck_admin.deps.hmac.compare_digest",
            wraps=__import__("hmac").compare_digest,
        ) as compare,
    ):
        await require_deck_admin_key("correct")
    compare.assert_called_once_with(b"correct", b"correct")


@pytest.mark.asyncio
async def test_list_decks_accepts_string_generation_mode_metrics() -> None:
    """Local-svg demo decks store string mode markers; listing must not 500."""
    from app.deck_admin.service import DeckAdminService

    deck_id = uuid4()
    repository = SimpleNamespace(
        list_decks=AsyncMock(
            return_value=[
                {
                    "id": deck_id,
                    "region_tag": "functional-demo",
                    "status": "ready",
                    "generation_metrics": {
                        "cost_microusd": 0,
                        "generation_mode": "local-svg",
                    },
                    "failure_reason": None,
                    "activated_at": None,
                    "created_at": datetime(2026, 7, 11, tzinfo=UTC),
                    "card_count": 5,
                }
            ]
        )
    )
    service = DeckAdminService(repository, SimpleNamespace(), data_dir=Path("data"))
    response = await service.list_decks()
    assert len(response.decks) == 1
    assert response.decks[0].generation_metrics == {
        "cost_microusd": 0,
        "generation_mode": "local-svg",
    }


@pytest.mark.asyncio
async def test_generate_deck_schedules_background_work() -> None:
    """Return generating response while leaving work in BackgroundTasks."""
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
    assert response.deck_id == deck_id
    assert response.status is DeckStatus.GENERATING
    service.start_generation.assert_awaited_once()
    service.run_generation.assert_not_awaited()
    assert len(tasks.tasks) == 1


@pytest.mark.asyncio
async def test_generation_failure_is_bounded_and_marks_failed() -> None:
    """Convert generator exceptions to bounded, non-sensitive failure state."""
    deck_id = uuid4()
    repository = SimpleNamespace(mark_failed=AsyncMock())
    generator = SimpleNamespace(
        generate=AsyncMock(side_effect=RuntimeError("secret-token-should-not-leak"))
    )
    service = DeckAdminService(repository, generator, data_dir=Path("data"))
    await service.run_generation(deck_id, _request())
    generator.generate.assert_awaited_once()
    repository.mark_failed.assert_awaited_once()
    reason = repository.mark_failed.await_args.args[1]
    assert reason == "Generation failed (RuntimeError)"
    assert "secret-token" not in reason
    assert len(reason) <= 500


@pytest.mark.asyncio
async def test_gateway_converts_operator_concepts_and_builds_existing_deck() -> None:
    """Call the evolving deckgen seam with existing-id and ready final status."""
    deck_id = uuid4()
    concepts = [{"concept_id": "pot", "label_en": "Pot"}]
    converted = [object()]
    converter = Mock(return_value=converted)
    builder = AsyncMock()

    def fake_import(name: str) -> SimpleNamespace:
        """Return isolated fake deckgen modules for the gateway."""
        if name == "deckgen.concepts":
            return SimpleNamespace(concepts_from_operator=converter)
        return SimpleNamespace(build_deck=builder)

    with patch("app.deck_admin.generation.import_module", side_effect=fake_import):
        await DeckgenGateway().generate(
            region_tag="assam",
            concepts=concepts,
            deck_id=deck_id,
        )
    converter.assert_called_once_with(concepts)
    builder.assert_awaited_once_with(
        region="assam",
        concepts=converted,
        deck_id=deck_id,
        final_status="ready",
    )


@pytest.mark.asyncio
async def test_review_returns_concepts_and_safe_media_urls() -> None:
    """Map stored card paths to review metadata without inline image content."""
    deck_id = uuid4()
    card_id = uuid4()
    row = _deck_row(deck_id)
    row["cards"] = [
        {
            "id": card_id,
            "concept_id": "concept_0",
            "image_path": f"decks/{deck_id}/{card_id}.png",
            "label_common": {"en": "Water pot", "as": "কলহ"},
            "verified": True,
        }
    ]
    repository = SimpleNamespace(get_deck=AsyncMock(return_value=row))
    service = DeckAdminService(repository, Mock(), data_dir=Path("data"))
    detail = await service.review_deck(deck_id)
    assert len(detail.concepts) == 6
    assert detail.cards[0].image_url == f"/media/decks/{deck_id}/{card_id}.png"
    assert detail.cards[0].label_en == "Water pot"
    assert not detail.cards[0].image_url.startswith("data:")


@pytest.mark.asyncio
async def test_review_rejects_image_path_outside_data_root(tmp_path: Path) -> None:
    """Prevent absolute paths outside DATA_DIR from becoming exposed URLs."""
    deck_id = uuid4()
    row = _deck_row(deck_id)
    row["cards"] = [
        {
            "id": uuid4(),
            "concept_id": "concept_0",
            "image_path": str(tmp_path.parent / "private.png"),
            "label_common": {"en": "Private"},
            "verified": True,
        }
    ]
    repository = SimpleNamespace(get_deck=AsyncMock(return_value=row))
    service = DeckAdminService(repository, Mock(), data_dir=tmp_path / "data")
    with pytest.raises(DeckAdminError) as caught:
        await service.review_deck(deck_id)
    assert caught.value.status_code == 500


@pytest.mark.asyncio
async def test_activation_allows_ready_and_idempotent_live() -> None:
    """Return live for both a ready transition and repeated live activation."""
    deck_id = uuid4()
    for original_status in ("ready", "live"):
        repository = SimpleNamespace(
            activate=AsyncMock(
                return_value={
                    "original_status": original_status,
                    "deck": {"id": deck_id, "status": "live"},
                }
            )
        )
        service = DeckAdminService(repository, Mock(), data_dir=Path("data"))
        response = await service.activate(deck_id)
        assert response.status is DeckStatus.LIVE


@pytest.mark.asyncio
async def test_activation_reports_missing_and_status_conflict() -> None:
    """Map absent targets to 404 and generating targets to 409."""
    deck_id = uuid4()
    missing = DeckAdminService(
        SimpleNamespace(activate=AsyncMock(return_value=None)),
        Mock(),
        data_dir=Path("data"),
    )
    with pytest.raises(DeckAdminError) as not_found:
        await missing.activate(deck_id)
    assert not_found.value.status_code == 404

    conflict = DeckAdminService(
        SimpleNamespace(
            activate=AsyncMock(
                return_value={"original_status": "generating", "deck": None}
            )
        ),
        Mock(),
        data_dir=Path("data"),
    )
    with pytest.raises(DeckAdminError) as not_ready:
        await conflict.activate(deck_id)
    assert not_ready.value.status_code == 409


class _Transaction:
    """Minimal async transaction context used by activation repository tests."""

    async def __aenter__(self) -> None:
        """Enter the fake transaction without side effects."""
        return None

    async def __aexit__(self, *_args: object) -> None:
        """Exit the fake transaction without suppressing exceptions."""
        return None


class _Connection:
    """Record activation SQL and supply scripted target/update rows."""

    def __init__(self, target_status: str) -> None:
        """Configure the target status returned under row lock."""
        self.target_status = target_status
        self.executed: list[str] = []
        self.deck_id: UUID | None = None

    def transaction(self) -> _Transaction:
        """Return a fake transaction context."""
        return _Transaction()

    async def execute(self, sql: str, *_args: object) -> str:
        """Record non-returning activation SQL."""
        self.executed.append(sql)
        return "UPDATE 1"

    async def fetchrow(self, sql: str, deck_id: UUID) -> dict[str, object]:
        """Return the locked target then the promoted live row."""
        self.executed.append(sql)
        self.deck_id = deck_id
        if "SELECT id, status" in sql:
            return {"id": deck_id, "status": self.target_status}
        return {"id": deck_id, "status": "live"}


class _Acquire:
    """Async pool-acquire context yielding one fake connection."""

    def __init__(self, connection: _Connection) -> None:
        """Store the fake connection."""
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        """Yield the configured connection."""
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        """Exit without suppressing exceptions."""
        return None


class _Pool:
    """Minimal activation-only pool fake."""

    def __init__(self, connection: _Connection) -> None:
        """Store the fake connection used for acquisition."""
        self.connection = connection

    def acquire(self) -> _Acquire:
        """Return an acquisition context for the fake connection."""
        return _Acquire(self.connection)


@pytest.mark.asyncio
async def test_repository_activation_serializes_demotes_and_promotes() -> None:
    """Use advisory lock before demoting live and promoting target atomically."""
    connection = _Connection("ready")
    repository = PostgresDeckAdminRepository(_Pool(connection))
    result = await repository.activate(uuid4())
    combined = "\n".join(connection.executed)
    assert result is not None
    assert result["original_status"] == "ready"
    assert "pg_advisory_xact_lock" in combined
    assert "SET status = 'ready'" in combined
    assert "SET status = 'live'" in combined
    assert combined.index("pg_advisory_xact_lock") < combined.index("SET status = 'ready'")
