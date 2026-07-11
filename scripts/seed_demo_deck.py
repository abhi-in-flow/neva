"""Seed a no-cost functional picture deck for isolated demo environments.

This operator script creates six recognizable emoji-based SVG cards, inserts
their multilingual labels and same-deck decoys, and atomically activates the
new deck. It never calls Gemini and is intended only for an explicitly marked
non-production environment when a generated deck is unavailable.

The default mode is a zero-I/O dry run. ``--execute`` is required to write
runtime files or mutate Postgres.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from uuid import UUID, uuid4

import asyncpg

from app.config import database_log_meta, get_settings

logger = logging.getLogger(__name__)

REQUIRED_ENVIRONMENTS = {"development", "demo", "load-test"}
DEMO_REGION_TAG = "functional-demo"
CARD_WIDTH = 768
CARD_HEIGHT = 768
CARD_SPECS = (
    ("fish", "🐟", "#0d7f8c", {"en": "fish", "hi": "मछली"}),
    ("tea", "☕", "#a5572a", {"en": "tea", "hi": "चाय"}),
    ("bicycle", "🚲", "#3557a6", {"en": "bicycle", "hi": "साइकिल"}),
    ("mango", "🥭", "#d89012", {"en": "mango", "hi": "आम"}),
    ("umbrella", "☂️", "#8c3f91", {"en": "umbrella", "hi": "छाता"}),
    ("lantern", "🏮", "#b33b32", {"en": "lantern", "hi": "लालटेन"}),
)


@dataclass(frozen=True)
class DemoCard:
    """Describe one deterministic card row and runtime SVG."""

    card_id: UUID
    concept_id: str
    glyph: str
    background: str
    labels: dict[str, str]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the explicit execution flag.

    Args:
        argv: Optional arguments excluding the process name.

    Returns:
        Namespace whose ``execute`` value controls all mutation.
    """
    logger.info("parse_args called argv_length=%s", 0 if argv is None else len(argv))
    parser = argparse.ArgumentParser(
        description="Seed an isolated no-cost functional demo deck.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write SVG cards and activate the deck; default is dry-run.",
    )
    return parser.parse_args(argv)


def build_cards() -> list[DemoCard]:
    """Build deterministic card content with fresh UUID identifiers.

    Returns:
        Six demo cards ready for filesystem and database publication.
    """
    logger.info("build_cards called card_count=%s", len(CARD_SPECS))
    return [
        DemoCard(
            card_id=uuid4(),
            concept_id=concept_id,
            glyph=glyph,
            background=background,
            labels=labels,
        )
        for concept_id, glyph, background, labels in CARD_SPECS
    ]


def render_svg(card: DemoCard) -> str:
    """Render one label-free, recognizable SVG card.

    Args:
        card: Card whose glyph and background should be rendered.

    Returns:
        UTF-8 SVG text containing no semantic label.
    """
    logger.info("render_svg called concept_id=%s card_id=%s", card.concept_id, card.card_id)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CARD_WIDTH}" '
        f'height="{CARD_HEIGHT}" viewBox="0 0 {CARD_WIDTH} {CARD_HEIGHT}">'
        f'<rect width="{CARD_WIDTH}" height="{CARD_HEIGHT}" rx="72" fill="{card.background}"/>'
        '<circle cx="384" cy="384" r="260" fill="#fff7e8" opacity="0.94"/>'
        f'<text x="384" y="470" text-anchor="middle" font-size="250" '
        'font-family="Apple Color Emoji, Segoe UI Emoji, Noto Color Emoji, sans-serif">'
        f"{card.glyph}</text></svg>"
    )


async def seed_demo_deck(*, execute: bool) -> dict[str, object]:
    """Plan or publish one functional demo deck.

    Args:
        execute: When false, return metadata without filesystem or DB I/O.

    Returns:
        Metadata describing dry-run status, deck ID, and card count.

    Side effects:
        In execute mode, writes SVGs below ``DATA_DIR/decks`` and atomically
        replaces the active deck in Postgres.
    """
    settings = get_settings()
    deck_id = uuid4()
    cards = build_cards()
    target_dir = settings.data_dir / "decks" / str(deck_id)
    summary: dict[str, object] = {
        "dry_run": not execute,
        "environment": settings.app_environment,
        "database": database_log_meta(settings.database_url),
        "data_dir": str(settings.data_dir),
        "deck_id": str(deck_id),
        "card_count": len(cards),
    }
    logger.info(
        "seed_demo_deck called execute=%s environment=%s deck_id=%s card_count=%s",
        execute,
        settings.app_environment,
        deck_id,
        len(cards),
    )
    if not execute:
        return summary
    if settings.app_environment not in REQUIRED_ENVIRONMENTS:
        raise RuntimeError("functional demo deck is forbidden outside non-production environments")

    target_dir.mkdir(parents=True, exist_ok=False)
    try:
        for card in cards:
            (target_dir / f"{card.card_id}.svg").write_text(
                render_svg(card),
                encoding="utf-8",
            )
        connection = await asyncpg.connect(settings.database_url)
        try:
            async with connection.transaction():
                await connection.execute(
                    "UPDATE decks SET status='ready', activated_at=NULL WHERE status='live'"
                )
                await connection.execute(
                    """
                    INSERT INTO decks (
                        id, region_tag, status, generation_input,
                        generation_metrics, activated_at
                    )
                    VALUES ($1, $2, 'live', $3::jsonb, $4::jsonb, now())
                    """,
                    deck_id,
                    DEMO_REGION_TAG,
                    json.dumps({"source": "no-cost-functional-demo"}),
                    json.dumps({"generation_mode": "local-svg", "cost_microusd": 0}),
                )
                for card in cards:
                    decoys = [
                        str(other.card_id)
                        for other in cards
                        if other.card_id != card.card_id
                    ]
                    await connection.execute(
                        """
                        INSERT INTO cards (
                            id, deck_id, concept_id, image_path,
                            label_common, decoys, verified
                        )
                        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, TRUE)
                        """,
                        card.card_id,
                        deck_id,
                        card.concept_id,
                        f"decks/{deck_id}/{card.card_id}.svg",
                        json.dumps(card.labels, ensure_ascii=False),
                        json.dumps(decoys),
                    )
        finally:
            await connection.close()
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    logger.info("seed_demo_deck completed deck_id=%s", deck_id)
    return summary


async def main(argv: list[str] | None = None) -> int:
    """Run the demo seed command.

    Args:
        argv: Optional arguments excluding the process name.

    Returns:
        Zero after successful dry-run or publication.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = parse_args(argv)
    summary = await seed_demo_deck(execute=args.execute)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
