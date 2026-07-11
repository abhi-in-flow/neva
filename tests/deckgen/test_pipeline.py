"""Tests for verification retries, rejection, decoys, metrics, and atomic publish.

Uses only fake GenAI clients and in-memory publishers. No Gemini API calls,
Postgres mutations, or runtime ``data/`` writes.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from deckgen.cli import main
from deckgen.client import SHARED_CLIENT_EXPECTATIONS, FakeDeckGenAIClient
from deckgen.concepts import CONCEPTS, select_concepts
from deckgen.config import COST_PER_IMAGE_USD, FAKE_IMAGE_BYTES, N_DECOYS
from deckgen.metrics import DeckMetrics
from deckgen.pipeline import (
    build_deck,
    generate_verified_image,
    map_decoy_concepts_to_card_uuids,
    select_decoys_batch,
    verification_accepted,
)
from deckgen.publish import CardRecord, InMemoryPublisher, PostgresPublisher

logger = logging.getLogger(__name__)


def _pass() -> dict:
    """Return an accepting verification payload."""
    return {
        "depicts_label": True,
        "has_text": False,
        "has_ambiguity": False,
        "competing_interpretation": None,
        "cultural_ok": True,
        "verdict": "pass",
        "reason": "ok",
    }


def _fail(*, cultural_ok: bool = True) -> dict:
    """Return a rejecting verification payload."""
    return {
        "depicts_label": False,
        "has_text": False,
        "has_ambiguity": True,
        "competing_interpretation": "other",
        "cultural_ok": cultural_ok,
        "verdict": "fail",
        "reason": "nope",
    }


def test_verification_accepted_requires_pass_depicts_and_no_text() -> None:
    """Accept only pass + depicts_label + !has_text + cultural_ok."""
    logger.info("test_verification_accepted_requires_pass_depicts_and_no_text")
    assert verification_accepted(_pass()) is True
    bad = _pass()
    bad["has_text"] = True
    assert verification_accepted(bad) is False
    bad2 = _pass()
    bad2["cultural_ok"] = False
    assert verification_accepted(bad2) is False


@pytest.mark.asyncio
async def test_generate_retries_then_accepts() -> None:
    """First verification fails, second passes within the retry budget."""
    logger.info("test_generate_retries_then_accepts")
    client = FakeDeckGenAIClient(verify_results=[_fail(), _pass()])
    metrics = DeckMetrics()
    concept = CONCEPTS[0]
    data = await generate_verified_image(
        client, concept, "Assamese village", metrics, max_retries=2
    )
    assert data == FAKE_IMAGE_BYTES
    assert metrics.images_attempted == 2
    assert metrics.images_rejected == 1
    assert metrics.images_accepted == 1
    verify_calls = [c for c in client.calls if c.get("operation") == "verify_image"]
    assert len(verify_calls) == 2


@pytest.mark.asyncio
async def test_generate_rejects_after_retries_exhausted() -> None:
    """All attempts fail → RuntimeError and reject counters populated."""
    logger.info("test_generate_rejects_after_retries_exhausted")
    client = FakeDeckGenAIClient(verify_results=[_fail(), _fail(), _fail()])
    metrics = DeckMetrics()
    with pytest.raises(RuntimeError, match="verification failed"):
        await generate_verified_image(
            client, CONCEPTS[0], "Assamese village", metrics, max_retries=2
        )
    assert metrics.images_attempted == 3
    assert metrics.images_rejected == 3
    assert metrics.images_accepted == 0


@pytest.mark.asyncio
async def test_decoy_selection_only_from_pool_and_maps_to_card_uuids() -> None:
    """Decoys must be pool concept ids, then map to same-deck card UUIDs."""
    logger.info("test_decoy_selection_only_from_pool_and_maps_to_card_uuids")
    concepts = select_concepts(6, seed=1)
    card_ids = [uuid.uuid4() for _ in concepts]
    from deckgen.pipeline import BuiltCard

    cards = [
        BuiltCard(
            card_id=cid,
            concept=concept,
            image_bytes=FAKE_IMAGE_BYTES,
            labels=dict(concept.labels),
        )
        for cid, concept in zip(card_ids, concepts, strict=True)
    ]
    pool_ids = [c.concept.id for c in cards]
    scripted = []
    for card in cards:
        decoys = [pid for pid in pool_ids if pid != card.concept.id][:N_DECOYS]
        scripted.append(
            {"card_id": str(card.card_id), "decoy_concept_ids": decoys}
        )
    client = FakeDeckGenAIClient(decoy_result=scripted)
    metrics = DeckMetrics()
    by_card = await select_decoys_batch(client, cards, metrics)
    assert set(by_card) == {str(c.card_id) for c in cards}
    for card in cards:
        decoys = by_card[str(card.card_id)]
        assert len(decoys) == N_DECOYS
        assert card.concept.id not in decoys
        assert set(decoys).issubset(set(pool_ids))

    uuid_map = map_decoy_concepts_to_card_uuids(cards, by_card)
    concept_to_uuid = {c.concept.id: str(c.card_id) for c in cards}
    for card in cards:
        expected = [concept_to_uuid[d] for d in by_card[str(card.card_id)]]
        assert uuid_map[str(card.card_id)] == expected
        assert str(card.card_id) not in uuid_map[str(card.card_id)]


@pytest.mark.asyncio
async def test_decoy_rejects_invented_concept_ids() -> None:
    """Decoy ids outside the generated pool raise ValueError."""
    logger.info("test_decoy_rejects_invented_concept_ids")
    concepts = select_concepts(6, seed=2)
    from deckgen.pipeline import BuiltCard

    cards = [
        BuiltCard(
            card_id=uuid.uuid4(),
            concept=concept,
            image_bytes=FAKE_IMAGE_BYTES,
            labels=dict(concept.labels),
        )
        for concept in concepts
    ]
    bad = [
        {
            "card_id": str(cards[0].card_id),
            "decoy_concept_ids": ["not-a-real-concept"] * N_DECOYS,
        }
    ]
    client = FakeDeckGenAIClient(decoy_result=bad)
    with pytest.raises(ValueError, match="outside pool"):
        await select_decoys_batch(client, cards, DeckMetrics())


def test_metrics_images_per_minute_cost_reject_total() -> None:
    """Metrics expose images/min, cost/image, reject rate, and total cost."""
    logger.info("test_metrics_images_per_minute_cost_reject_total")
    m = DeckMetrics()
    m.record_image_attempt()
    m.record_reject()
    m.record_image_attempt()
    m.record_accept()
    m.record_flash_call()
    m.finish()
    d = m.as_dict()
    assert d["images_attempted"] == 2
    assert d["images_rejected"] == 1
    assert d["images_accepted"] == 1
    assert d["reject_rate"] == 0.5
    assert d["images_per_minute"] > 0
    assert d["total_cost_usd"] > 0
    assert d["cost_per_image_usd"] > 0
    assert d["nb2_unit_cost_usd"] == COST_PER_IMAGE_USD


@pytest.mark.asyncio
async def test_atomic_publish_success_records_live_deck() -> None:
    """Successful in-memory publish marks the deck live with decoy UUIDs."""
    logger.info("test_atomic_publish_success_records_live_deck")
    pub = InMemoryPublisher()
    c1 = uuid.uuid4()
    c2 = uuid.uuid4()
    cards = [
        CardRecord(
            card_id=c1,
            concept_id="a",
            image_bytes=FAKE_IMAGE_BYTES,
            label_common={"en": "a"},
            decoy_card_ids=[str(c2)],
        ),
        CardRecord(
            card_id=c2,
            concept_id="b",
            image_bytes=FAKE_IMAGE_BYTES,
            label_common={"en": "b"},
            decoy_card_ids=[str(c1)],
        ),
    ]
    result = await pub.publish(region_tag="assam", cards=cards)
    assert result.status == "live"
    assert result.dry_run is True
    assert result.deck_id in pub.live_decks
    assert pub.published[0]["decoys"][str(c1)] == [str(c2)]


@pytest.mark.asyncio
async def test_atomic_publish_failure_does_not_mark_live() -> None:
    """Configured publish failure never records a live deck."""
    logger.info("test_atomic_publish_failure_does_not_mark_live")
    pub = InMemoryPublisher(fail=True)
    cards = [
        CardRecord(
            card_id=uuid.uuid4(),
            concept_id="a",
            image_bytes=FAKE_IMAGE_BYTES,
            label_common={"en": "a"},
            decoy_card_ids=[],
        )
    ]
    with pytest.raises(RuntimeError, match="simulated publish failure"):
        await pub.publish(region_tag="assam", cards=cards)
    assert pub.live_decks == []
    assert len(pub.published) == 1


@pytest.mark.asyncio
async def test_dry_run_pipeline_no_api_db_or_data_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full dry-run builds a deck without touching DATA_DIR or shared client."""
    logger.info("test_dry_run_pipeline_no_api_db_or_data_mutation")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    from deckgen.config import get_settings

    get_settings.cache_clear()

    client = FakeDeckGenAIClient()
    publisher = InMemoryPublisher()
    result = await build_deck(
        region="assam",
        cards=6,
        dry_run=True,
        client=client,
        publisher=publisher,
        seed=42,
    )
    assert result.dry_run is True
    assert result.publish is not None
    assert result.publish.status == "live"
    assert len(result.cards) == 6
    assert result.metrics.images_accepted == 6
    assert list(data_dir.iterdir()) == []
    assert all(
        c["method"] in {"generate_image", "generate_json"} for c in client.calls
    )
    all_card_ids = {str(c.card_id) for c in result.cards}
    for intent in publisher.published:
        for decoys in intent["decoys"].values():
            assert set(decoys).issubset(all_card_ids)
    get_settings.cache_clear()


def test_cli_dry_run_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI ``--dry-run`` completes and prints metrics JSON."""
    logger.info("test_cli_dry_run_exits_zero")
    code = main(
        ["--region", "assam", "--cards", "6", "--dry-run", "--seed", "7"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "images_per_minute" in out
    assert "total_cost_usd" in out
    assert "reject_rate" in out
    assert "cost_per_image_usd" in out


def test_shared_client_expectations_documented() -> None:
    """Shared-client contract string is non-empty for orchestrator handoff."""
    logger.info("test_shared_client_expectations_documented")
    assert "generate_images" in SHARED_CLIENT_EXPECTATIONS
    assert "generate_json" in SHARED_CLIENT_EXPECTATIONS
    assert "generate_content" in SHARED_CLIENT_EXPECTATIONS
    assert "MediaBlob" in SHARED_CLIENT_EXPECTATIONS


@pytest.mark.asyncio
async def test_postgres_publisher_uses_transaction_and_live_flip(
    tmp_path: Path,
) -> None:
    """Postgres publisher writes images then inserts inside a transaction."""
    logger.info("test_postgres_publisher_uses_transaction_and_live_flip")
    data_dir = tmp_path / "data"
    pub = PostgresPublisher(
        database_url="postgresql://u:p@localhost:5432/db",
        data_dir=data_dir,
    )
    card_id = uuid.uuid4()
    cards = [
        CardRecord(
            card_id=card_id,
            concept_id="kalash",
            image_bytes=FAKE_IMAGE_BYTES,
            label_common={"en": "water pot"},
            decoy_card_ids=[],
        )
    ]

    mock_conn = AsyncMock()
    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=None)
    txn.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction = lambda: txn
    mock_conn.execute = AsyncMock()
    mock_conn.close = AsyncMock()

    with patch("deckgen.publish.asyncpg.connect", new=AsyncMock(return_value=mock_conn)):
        result = await pub.publish(region_tag="assam", cards=cards)

    assert result.status == "live"
    assert result.dry_run is False
    written = data_dir / "decks" / str(result.deck_id) / f"{card_id}.png"
    assert written.exists()
    assert written.read_bytes() == FAKE_IMAGE_BYTES
    assert mock_conn.execute.await_count >= 3
    sqls = [call.args[0] for call in mock_conn.execute.await_args_list]
    assert any("INSERT INTO decks" in s for s in sqls)
    assert any("INSERT INTO cards" in s for s in sqls)
    assert any("UPDATE decks SET status" in s for s in sqls)
