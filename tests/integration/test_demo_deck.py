"""Tests for the explicit no-cost functional demo deck seeder."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.seed_demo_deck import build_cards, render_svg, seed_demo_deck


def test_demo_svg_never_contains_semantic_labels() -> None:
    """Card SVGs expose only the picture glyph, never answer text."""
    for card in build_cards():
        svg = render_svg(card).lower()
        assert card.concept_id not in svg
        assert all(label.lower() not in svg for label in card.labels.values())


@pytest.mark.asyncio
async def test_demo_deck_dry_run_has_zero_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Default dry-run neither connects to Postgres nor creates deck files."""

    async def forbidden_connect(_database_url: str) -> None:
        """Fail if the dry-run opens a database connection."""
        raise AssertionError("dry-run must not connect")

    monkeypatch.setattr("scripts.seed_demo_deck.asyncpg.connect", forbidden_connect)
    monkeypatch.setattr(
        "scripts.seed_demo_deck.get_settings",
        lambda: SimpleNamespace(
            app_environment="demo",
            database_url="postgresql://u:p@127.0.0.1/demo",
            data_dir=tmp_path,
        ),
    )

    result = await seed_demo_deck(execute=False)

    assert result["dry_run"] is True
    assert not (tmp_path / "decks").exists()
