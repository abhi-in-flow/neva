"""Matchmaking and package-protocol unit tests against the memory store."""

from __future__ import annotations

import logging

from app.game.audio_checks import AudioCheckResult
from app.game.config import GameFeatureConfig
from app.game.memory_store import MemoryGameStore
from app.game.service import GameService
from app.game.tokens import hash_session_token, issue_session_token

logger = logging.getLogger(__name__)


def _accept(path, *, byte_length, config=None) -> AudioCheckResult:
    """Accepting audio checker for service-level tests."""
    _ = (path, byte_length, config)
    return AudioCheckResult(accepted=True, duration_s=2.0, mean_volume_db=-12.0)

async def test_matchmaking_requires_shared_common_and_different_native(
    tmp_path,
) -> None:
    """Refuse to pair same-native or non-overlapping common languages.

    Args:
        tmp_path: Temporary data directory.
    """
    logger.info("test_matchmaking_requires_shared_common_and_different_native called")
    store = MemoryGameStore()
    await store.seed_deck(
        region_tag="t",
        cards=[
            {
                "image_path": "decks/t/1.png",
                "label_common": {"en": "cup"},
                "decoys": [],
            }
        ],
    )
    service = GameService(
        store,
        data_dir=tmp_path,
        rounds_cap=20,
        config=GameFeatureConfig(result_hold_seconds=0),
    )
    a = await service.join(nickname="A", native_lang="hindi", common_langs=["english"])
    b = await service.join(nickname="B", native_lang="hindi", common_langs=["english"])
    player_a = await service.resolve_player(a.session_token)
    player_b = await service.resolve_player(b.session_token)
    await service.request_pair(player_a)
    result = await service.request_pair(player_b)
    assert result["status"] == "queued"
    assert await store.get_active_pair(player_a.id) is None

    c = await service.join(nickname="C", native_lang="tamil", common_langs=["english"])
    player_c = await service.resolve_player(c.session_token)
    matched = await service.request_pair(player_c)
    assert matched["status"] == "matched"
    logger.info("test_matchmaking_requires_shared_common_and_different_native completed")


async def test_package_enqueued_only_when_scored_and_quality_present(
    tmp_path,
) -> None:
    """Package job appears after scoring only if quality JSON exists.

    Args:
        tmp_path: Temporary data directory.
    """
    logger.info("test_package_enqueued_only_when_scored_and_quality_present called")
    store = MemoryGameStore()
    await store.seed_deck(
        region_tag="t",
        cards=[
            {
                "image_path": "decks/t/1.png",
                "label_common": {"en": "lamp"},
            },
            {
                "image_path": "decks/t/2.png",
                "label_common": {"en": "rope"},
            },
        ],
    )
    service = GameService(
        store,
        data_dir=tmp_path,
        rounds_cap=20,
        config=GameFeatureConfig(result_hold_seconds=0),
        audio_checker=_accept,
    )
    a = await service.join(nickname="A", native_lang="assamese", common_langs=["hindi"])
    b = await service.join(nickname="B", native_lang="tamil", common_langs=["hindi"])
    pa = await service.resolve_player(a.session_token)
    pb = await service.resolve_player(b.session_token)
    await service.request_pair(pa)
    await service.request_pair(pb)
    pair = await store.get_active_pair(pa.id)
    assert pair is not None
    turn = await store.get_latest_turn(pair.id)
    assert turn is not None

    speaker = pa if turn.speaker_id == pa.id else pb
    guesser = pb if speaker is pa else pa
    await service.upload_audio(speaker, payload=b"abc")
    await service.confirm_label(speaker)
    # Score without quality → no package.
    await service.guess(guesser, option_id=turn.card_id)
    assert await store.count_jobs(kind="package", turn_id=turn.id) == 0

    # New turn: set quality then score → package.
    turn2 = await store.get_latest_turn(pair.id)
    assert turn2 is not None and turn2.id != turn.id
    speaker2 = pa if turn2.speaker_id == pa.id else pb
    guesser2 = pb if speaker2 is pa else pa
    await service.upload_audio(speaker2, payload=b"abc")
    await service.confirm_label(speaker2)
    await store.set_turn_quality_for_tests(turn2.id, {"is_speech": True})
    await service.guess(guesser2, option_id=turn2.card_id)
    assert await store.count_jobs(kind="package", turn_id=turn2.id) == 1
    logger.info("test_package_enqueued_only_when_scored_and_quality_present completed")


async def test_token_hash_round_trip() -> None:
    """Session tokens hash stably for lookup."""
    logger.info("test_token_hash_round_trip called")
    token = issue_session_token()
    assert hash_session_token(token) == hash_session_token(token)
    assert len(hash_session_token(token)) == 64
    logger.info("test_token_hash_round_trip completed")
