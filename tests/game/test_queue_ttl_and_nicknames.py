"""Queue TTL and nickname uniqueness tests against the memory store.

Covers stale-queue exclusion, enqueue heartbeat refresh, case-insensitive
collision suffixes, max-length bounding, and concurrent join reservation
semantics. Uses only the in-memory store — no live Postgres mutation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.game.config import GameFeatureConfig
from app.game.memory_store import MemoryGameStore
from app.game.nicknames import NICKNAME_MAX_LEN, allocate_nickname_candidate
from app.game.service import GameService
from app.game.tokens import hash_session_token, issue_session_token

logger = logging.getLogger(__name__)


async def _seed_minimal_deck(store: MemoryGameStore) -> None:
    """Insert a one-card live deck for matchmaking tests.

    Args:
        store: Target memory store.
    """
    logger.info("_seed_minimal_deck called")
    await store.seed_deck(
        region_tag="ttl",
        cards=[
            {
                "image_path": "decks/ttl/1.png",
                "label_common": {"en": "cup"},
                "decoys": [],
            }
        ],
    )


async def test_stale_queue_player_never_selected(tmp_path) -> None:
    """Evict abandoned queue rows so they cannot match a fresh player.

    Args:
        tmp_path: Temporary data directory.
    """
    logger.info("test_stale_queue_player_never_selected called")
    store = MemoryGameStore()
    await _seed_minimal_deck(store)
    service = GameService(
        store,
        data_dir=tmp_path,
        rounds_cap=20,
        config=GameFeatureConfig(result_hold_seconds=0),
    )
    stale_join = await service.join(
        nickname="Stale",
        native_lang="hindi",
        common_langs=["english"],
    )
    fresh_join = await service.join(
        nickname="Fresh",
        native_lang="tamil",
        common_langs=["english"],
    )
    stale = await service.resolve_player(stale_join.session_token)
    fresh = await service.resolve_player(fresh_join.session_token)

    await service.request_pair(stale)
    assert stale.id in store.queue
    # Simulate an abandoned heartbeat older than the default 30s TTL.
    store.queue[stale.id] = datetime.now(timezone.utc) - timedelta(seconds=60)

    result = await service.request_pair(fresh)
    assert result["status"] == "queued"
    assert await store.get_active_pair(fresh.id) is None
    assert stale.id not in store.queue
    assert fresh.id in store.queue
    logger.info("test_stale_queue_player_never_selected completed")


async def test_enqueue_refreshes_enqueued_at(tmp_path) -> None:
    """Every pair/request heartbeat must bump the queue timestamp.

    Args:
        tmp_path: Temporary data directory.
    """
    logger.info("test_enqueue_refreshes_enqueued_at called")
    store = MemoryGameStore()
    service = GameService(
        store,
        data_dir=tmp_path,
        rounds_cap=20,
        config=GameFeatureConfig(result_hold_seconds=0),
    )
    joined = await service.join(
        nickname="Waiter",
        native_lang="bengali",
        common_langs=["english"],
    )
    player = await service.resolve_player(joined.session_token)
    await store.enqueue_player(player.id)
    first = store.queue[player.id]
    await asyncio.sleep(0.02)
    await store.enqueue_player(player.id)
    second = store.queue[player.id]
    assert second > first
    logger.info("test_enqueue_refreshes_enqueued_at completed")


async def test_active_pair_not_harmed_by_queue_ttl(tmp_path) -> None:
    """Already-matched players stay paired regardless of queue TTL.

    Args:
        tmp_path: Temporary data directory.
    """
    logger.info("test_active_pair_not_harmed_by_queue_ttl called")
    store = MemoryGameStore()
    await _seed_minimal_deck(store)
    service = GameService(
        store,
        data_dir=tmp_path,
        rounds_cap=20,
        config=GameFeatureConfig(result_hold_seconds=0),
    )
    a = await service.join(nickname="PairedA", native_lang="hindi", common_langs=["english"])
    b = await service.join(nickname="PairedB", native_lang="tamil", common_langs=["english"])
    pa = await service.resolve_player(a.session_token)
    pb = await service.resolve_player(b.session_token)
    await service.request_pair(pa)
    matched = await service.request_pair(pb)
    assert matched["status"] == "matched"
    pair = await store.get_active_pair(pa.id)
    assert pair is not None

    # Re-request while paired must report matched and leave the pair intact.
    again = await service.request_pair(pa)
    assert again["status"] == "matched"
    assert await store.get_active_pair(pa.id) is not None
    assert pa.id not in store.queue
    assert pb.id not in store.queue
    logger.info("test_active_pair_not_harmed_by_queue_ttl completed")


async def test_nickname_preserves_requested_and_suffixes_collisions(tmp_path) -> None:
    """First joiner keeps the friendly name; collisions get a compact suffix.

    Args:
        tmp_path: Temporary data directory.
    """
    logger.info("test_nickname_preserves_requested_and_suffixes_collisions called")
    store = MemoryGameStore()
    service = GameService(
        store,
        data_dir=tmp_path,
        rounds_cap=20,
        config=GameFeatureConfig(result_hold_seconds=0),
    )
    first = await service.join(
        nickname="Maya",
        native_lang="hindi",
        common_langs=["english"],
    )
    second = await service.join(
        nickname="maya",
        native_lang="tamil",
        common_langs=["english"],
    )
    third = await service.join(
        nickname="MAYA",
        native_lang="bengali",
        common_langs=["english"],
    )
    p1 = await service.resolve_player(first.session_token)
    p2 = await service.resolve_player(second.session_token)
    p3 = await service.resolve_player(third.session_token)
    assert p1.nickname == "Maya"
    assert p2.nickname == "maya#2"
    assert p3.nickname == "MAYA#3"
    lowers = {p1.nickname.casefold(), p2.nickname.casefold(), p3.nickname.casefold()}
    assert len(lowers) == 3
    logger.info("test_nickname_preserves_requested_and_suffixes_collisions completed")


async def test_nickname_suffix_respects_max_length() -> None:
    """Collision suffixes truncate the base so nicknames stay within 32 chars.

    Returns:
        None.
    """
    logger.info("test_nickname_suffix_respects_max_length called")
    base = "A" * NICKNAME_MAX_LEN
    exact = allocate_nickname_candidate(base, 0)
    assert exact == base
    assert len(exact) == NICKNAME_MAX_LEN
    collided = allocate_nickname_candidate(base, 1)
    assert len(collided) <= NICKNAME_MAX_LEN
    assert collided.endswith("#2")
    assert collided.startswith("A")
    logger.info("test_nickname_suffix_respects_max_length completed")


async def test_concurrent_joins_reserve_unique_nicknames() -> None:
    """Concurrent create_player calls must not share a case-insensitive name."""
    logger.info("test_concurrent_joins_reserve_unique_nicknames called")
    store = MemoryGameStore()

    async def _join_one(index: int):
        """Create one player requesting the same friendly nickname.

        Args:
            index: Distinguishes session token hashes.

        Returns:
            Created player record.
        """
        token = issue_session_token()
        return await store.create_player(
            nickname="River",
            native_lang="hindi" if index % 2 == 0 else "tamil",
            common_langs=["english"],
            session_token_hash=hash_session_token(f"{token}-{index}"),
        )

    players = await asyncio.gather(*[_join_one(i) for i in range(8)])
    lowers = [p.nickname.casefold() for p in players]
    assert len(set(lowers)) == len(lowers)
    assert any(p.nickname == "River" for p in players)
    for player in players:
        assert 1 <= len(player.nickname) <= NICKNAME_MAX_LEN
    logger.info("test_concurrent_joins_reserve_unique_nicknames completed")


async def test_active_refresh_prevents_eviction_then_matches(tmp_path) -> None:
    """A refreshed waiter remains matchable after a stale peer is evicted.

    Args:
        tmp_path: Temporary data directory.
    """
    logger.info("test_active_refresh_prevents_eviction_then_matches called")
    store = MemoryGameStore()
    await _seed_minimal_deck(store)
    service = GameService(
        store,
        data_dir=tmp_path,
        rounds_cap=20,
        config=GameFeatureConfig(result_hold_seconds=0),
    )
    stale_join = await service.join(
        nickname="OldPeer",
        native_lang="hindi",
        common_langs=["english"],
    )
    active_join = await service.join(
        nickname="ActivePeer",
        native_lang="assamese",
        common_langs=["english"],
    )
    new_join = await service.join(
        nickname="NewPeer",
        native_lang="tamil",
        common_langs=["english"],
    )
    stale = await service.resolve_player(stale_join.session_token)
    active = await service.resolve_player(active_join.session_token)
    new = await service.resolve_player(new_join.session_token)

    await service.request_pair(stale)
    await service.request_pair(active)
    store.queue[stale.id] = datetime.now(timezone.utc) - timedelta(seconds=90)
    # Heartbeat refresh for the active waiter.
    await store.enqueue_player(active.id)

    matched = await service.request_pair(new)
    assert matched["status"] == "matched"
    pair = await store.get_active_pair(new.id)
    assert pair is not None
    members = {pair.player_a, pair.player_b}
    assert active.id in members
    assert new.id in members
    assert stale.id not in members
    assert stale.id not in store.queue
    logger.info("test_active_refresh_prevents_eviction_then_matches completed")


async def test_match_when_partner_lists_other_native(tmp_path) -> None:
    """Pair players whose only shared language is one player's mother tongue.

    Join stores native separately from ``common_langs``. Without speakable-set
    matching, Assamese native + Telugu native who listed Assamese never paired.

    Args:
        tmp_path: Temporary data directory.
    """
    logger.info("test_match_when_partner_lists_other_native called")
    store = MemoryGameStore()
    await _seed_minimal_deck(store)
    service = GameService(
        store,
        data_dir=tmp_path,
        rounds_cap=20,
        config=GameFeatureConfig(result_hold_seconds=0),
    )

    assamese = await service.join(
        nickname="Nimbu Dynamo",
        native_lang="as",
        common_langs=["hi", "en"],
    )
    telugu = await service.join(
        nickname="Monsoon Rockstar",
        native_lang="te",
        common_langs=["as", "kn"],
    )
    player_a = await service.resolve_player(assamese.session_token)
    player_b = await service.resolve_player(telugu.session_token)

    first = await service.request_pair(player_a)
    assert first["status"] == "queued"
    second = await service.request_pair(player_b)
    assert second["status"] == "matched"

    pair = await store.get_active_pair(player_a.id)
    assert pair is not None
    assert pair.common_lang == "as"
    assert {pair.player_a, pair.player_b} == {player_a.id, player_b.id}
    logger.info("test_match_when_partner_lists_other_native completed")


async def test_match_complementary_native_and_common(tmp_path) -> None:
    """Pair English↔Hindi natives who each listed the other's language only.

    Args:
        tmp_path: Temporary data directory.
    """
    logger.info("test_match_complementary_native_and_common called")
    store = MemoryGameStore()
    await _seed_minimal_deck(store)
    service = GameService(
        store,
        data_dir=tmp_path,
        rounds_cap=20,
        config=GameFeatureConfig(result_hold_seconds=0),
    )

    english = await service.join(
        nickname="EnSpeaker",
        native_lang="en",
        common_langs=["hi"],
    )
    hindi = await service.join(
        nickname="HiSpeaker",
        native_lang="hi",
        common_langs=["en"],
    )
    player_a = await service.resolve_player(english.session_token)
    player_b = await service.resolve_player(hindi.session_token)

    await service.request_pair(player_a)
    result = await service.request_pair(player_b)
    assert result["status"] == "matched"
    pair = await store.get_active_pair(player_a.id)
    assert pair is not None
    assert pair.common_lang == "en"
    logger.info("test_match_complementary_native_and_common completed")


async def test_prefer_english_when_shared_with_other_langs(tmp_path) -> None:
    """Prefer English card labels when English is among several shared langs.

    Args:
        tmp_path: Temporary data directory.
    """
    logger.info("test_prefer_english_when_shared_with_other_langs called")
    store = MemoryGameStore()
    await _seed_minimal_deck(store)
    service = GameService(
        store,
        data_dir=tmp_path,
        rounds_cap=20,
        config=GameFeatureConfig(result_hold_seconds=0),
    )

    assamese = await service.join(
        nickname="AsPlayer",
        native_lang="as",
        common_langs=["hi", "en"],
    )
    hindi = await service.join(
        nickname="HiPlayer",
        native_lang="hi",
        common_langs=["en", "kn"],
    )
    player_a = await service.resolve_player(assamese.session_token)
    player_b = await service.resolve_player(hindi.session_token)

    await service.request_pair(player_a)
    result = await service.request_pair(player_b)
    assert result["status"] == "matched"
    pair = await store.get_active_pair(player_a.id)
    assert pair is not None
    assert pair.common_lang == "en"
    logger.info("test_prefer_english_when_shared_with_other_langs completed")
