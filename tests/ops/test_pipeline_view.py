"""Smoke tests for the sanitized operator pipeline viewer CLI."""

from __future__ import annotations

from scripts.pipeline_view import build_fixture_snapshot, main, render_snapshot


def test_fixture_snapshot_is_marked_and_eligible() -> None:
    """Fixture mode must never claim to be live venue data."""
    snapshot = build_fixture_snapshot("demo-turn")
    assert snapshot.source == "fixture"
    assert snapshot.training_eligible is True
    text = render_snapshot(snapshot)
    assert "source=fixture" in text
    assert "6_shard" in text
    assert "demo-turn" in text


def test_main_fixture_dry_run(capsys) -> None:
    """CLI fixture mode exits zero without touching Postgres."""
    assert main(["--fixture"]) == 0
    out = capsys.readouterr().out
    assert "PIPELINE VIEW" in out
    assert "source=fixture" in out
