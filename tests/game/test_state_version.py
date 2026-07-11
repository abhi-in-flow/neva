"""Contract tests for polling version changes outside the active turn.

The frontend skips rendering when ``state_version`` is unchanged, so rank and
leaderboard changes must participate in the fingerprint even if the player's
phase, score, pair, and turn are stable.
"""

from __future__ import annotations

from uuid import uuid4

from app.game.state import compute_state_version
from app.game.types import LeaderboardRow
from contracts.api_types import Phase


def test_leaderboard_change_updates_state_version() -> None:
    """Ensure a changed leaderboard invalidates the polling response version."""
    player_id = uuid4()
    baseline = compute_state_version(
        phase=Phase.QUEUED,
        player_id=player_id,
        pair_id=None,
        turn_id=None,
        turn_status=None,
        attempts=0,
        score=0,
        rank=2,
        rounds_played=0,
        leaderboard_top=[LeaderboardRow("A", 10), LeaderboardRow("B", 0)],
        queued=True,
    )
    changed = compute_state_version(
        phase=Phase.QUEUED,
        player_id=player_id,
        pair_id=None,
        turn_id=None,
        turn_status=None,
        attempts=0,
        score=0,
        rank=2,
        rounds_played=0,
        leaderboard_top=[LeaderboardRow("A", 20), LeaderboardRow("B", 0)],
        queued=True,
    )

    assert changed != baseline
