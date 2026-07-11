"""Seed a verified no-cost deck for isolated end-to-end acceptance."""

from __future__ import annotations

import json
import logging
from uuid import uuid4

import asyncpg

from tests.integration.config import Wave2E2EConfig

LOGGER = logging.getLogger(__name__)
MINIMAL_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
SEED_LABELS = ("fish", "tea", "bicycle", "mango", "umbrella", "lantern")


async def seed_live_deck(config: Wave2E2EConfig) -> dict[str, object]:
    """Insert one live deck with six verified cards and PNG fixtures.

    Args:
        config: Guarded end-to-end configuration.

    Returns:
        Seeded deck and card identifiers.

    Side effects:
        Writes isolated PNG files and inserts deck/card rows.
    """
    LOGGER.info("seed_live_deck called data_dir=%s", config.data_dir)
    deck_id = uuid4()
    card_ids = [uuid4() for _ in SEED_LABELS]
    cards: list[dict[str, object]] = []
    for card_id, label in zip(card_ids, SEED_LABELS, strict=True):
        image_path = f"decks/{deck_id}/{card_id}.png"
        absolute = config.data_dir / image_path
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_bytes(MINIMAL_PNG_BYTES)
        cards.append(
            {
                "card_id": str(card_id),
                "image_path": image_path,
                "label_common": {"en": label, "hi": label},
            }
        )

    connection = await asyncpg.connect(config.database_url)
    try:
        async with connection.transaction():
            await connection.execute(
                """
                INSERT INTO decks (id, region_tag, status, activated_at)
                VALUES ($1::uuid, $2, 'live', now())
                """,
                str(deck_id),
                "wave2_e2e",
            )
            for index, card in enumerate(cards):
                decoys = [str(value) for i, value in enumerate(card_ids) if i != index][:5]
                await connection.execute(
                    """
                    INSERT INTO cards (
                        id, deck_id, image_path, label_common, decoys, verified
                    )
                    VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5::jsonb, TRUE)
                    """,
                    card["card_id"],
                    str(deck_id),
                    card["image_path"],
                    json.dumps(card["label_common"]),
                    json.dumps(decoys),
                )
    finally:
        await connection.close()
    return {
        "deck_id": str(deck_id),
        "card_ids": [str(card_id) for card_id in card_ids],
        "labels": list(SEED_LABELS),
        "card_count": len(cards),
    }


def label_in_state_payload(payload: object, label: str) -> bool:
    """Return whether a state payload exposes one semantic label.

    Args:
        payload: Parsed state payload.
        label: Label to search for.

    Returns:
        Whether a ``label.text`` representation is present.
    """
    LOGGER.info("label_in_state_payload called label=%s", label)
    text = str(payload)
    return f'"text": "{label}"' in text or f"'text': '{label}'" in text
