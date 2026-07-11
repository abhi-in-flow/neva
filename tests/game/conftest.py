"""Shared fixtures for isolated game-core tests.

Builds an in-memory ``GameService`` with temporary audio paths and a FastAPI
app that mounts only game routers. No live Postgres mutation and no Gemini
calls occur in this suite.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_game_service
from app.api.routers import include_game_routers
from app.game.audio_checks import AudioCheckResult
from app.game.config import GameFeatureConfig
from app.game.memory_store import MemoryGameStore
from app.game.service import GameService

logger = logging.getLogger(__name__)


def _accepting_audio_checker(path: Path, *, byte_length: int, config=None) -> AudioCheckResult:
    """Test double that accepts any non-empty upload without ffmpeg.

    Args:
        path: Saved upload path.
        byte_length: Upload size.
        config: Unused feature config.

    Returns:
        Accepted result with a mid-range duration.
    """
    _ = (path, config)
    logger.info(
        "_accepting_audio_checker called path_name=%s byte_length=%s",
        path.name,
        byte_length,
    )
    return AudioCheckResult(accepted=True, duration_s=2.5, mean_volume_db=-16.0)


@pytest.fixture
def game_config() -> GameFeatureConfig:
    """Feature config with zero result-hold for deterministic phase asserts.

    Returns:
        Frozen config suitable for smoke tests.
    """
    logger.info("game_config fixture setup")
    return GameFeatureConfig(result_hold_seconds=0.0, turn_deadline_seconds=90)


@pytest.fixture
def memory_store() -> MemoryGameStore:
    """Fresh in-memory store for one test.

    Returns:
        Empty ``MemoryGameStore``.
    """
    logger.info("memory_store fixture setup")
    return MemoryGameStore()


@pytest.fixture
async def seeded_store(memory_store: MemoryGameStore) -> MemoryGameStore:
    """Memory store with a five-card live deck.

    Args:
        memory_store: Empty store fixture.

    Returns:
        Store containing five verified cards with cross decoys.
    """
    logger.info("seeded_store fixture setup")
    cards = [
        {
            "image_path": f"decks/seed/card_{index}.png",
            "label_common": {"en": label, "hi": label},
        }
        for index, label in enumerate(
            ["fish", "tea", "bicycle", "mango", "umbrella"],
            start=1,
        )
    ]
    await memory_store.seed_deck(region_tag="seed", cards=cards)
    return memory_store


@pytest.fixture
def game_service(
    seeded_store: MemoryGameStore,
    tmp_path: Path,
    game_config: GameFeatureConfig,
) -> GameService:
    """Game service bound to the seeded memory store and temp data dir.

    Args:
        seeded_store: Store with five cards.
        tmp_path: Pytest temp directory.
        game_config: Test feature config.

    Returns:
        Configured ``GameService`` with a fake accepting audio checker.
    """
    logger.info("game_service fixture setup")
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "audio").mkdir(exist_ok=True)
    return GameService(
        seeded_store,
        data_dir=data_dir,
        rounds_cap=20,
        config=game_config,
        audio_checker=_accepting_audio_checker,
    )


@pytest.fixture
async def game_client(game_service: GameService) -> AsyncIterator[AsyncClient]:
    """HTTP client against a game-only FastAPI app with DI overrides.

    Args:
        game_service: Injected service for all routes.

    Yields:
        ``AsyncClient`` targeting the in-process ASGI app.
    """
    logger.info("game_client fixture setup")
    app = FastAPI(title="Game Core Test App")
    include_game_routers(app)
    app.dependency_overrides[get_game_service] = lambda: game_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
    logger.info("game_client fixture teardown")


@pytest.fixture
def auth_header() -> Iterator[None]:
    """Placeholder fixture documenting bearer header helpers.

    Yields:
        None. Tests build headers inline via ``bearer``.
    """
    yield None


def bearer(token: str) -> dict[str, str]:
    """Build an Authorization header map for a session token.

    Args:
        token: Raw session token from ``/api/join``.

    Returns:
        Header dict with Bearer authorization.
    """
    return {"Authorization": f"Bearer {token}"}
