"""Persistence protocol for game-core operations.

Defines the store interface implemented by the Postgres adapter and the
in-memory test double. Services depend only on this protocol so smoke tests
never mutate the live database.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from app.game.types import (
    CardRecord,
    LeaderboardRow,
    MetricsSnapshot,
    PairRecord,
    PlayerRecord,
    PlayerStats,
    StateBundle,
    TurnRecord,
)


class GameStore(Protocol):
    """Async persistence boundary for game-core use cases."""

    async def create_player(
        self,
        *,
        nickname: str,
        native_lang: str,
        common_langs: list[str],
        session_token_hash: str,
    ) -> PlayerRecord:
        """Insert a player and return the created row.

        Persisted nicknames are case-insensitively unique. The requested
        friendly name is reserved when available; collisions receive a compact
        bounded suffix within the 32-character limit.
        """

    async def get_player_by_token_hash(self, token_hash: str) -> PlayerRecord | None:
        """Lookup a player by hashed bearer token."""

    async def get_player(self, player_id: UUID) -> PlayerRecord | None:
        """Lookup a player by id."""

    async def enqueue_player(self, player_id: UUID) -> None:
        """Insert or refresh the player in the matchmaking queue.

        Every ``pair/request`` heartbeat must update ``enqueued_at`` so active
        waiters stay inside the queue activity TTL.

        Args:
            player_id: Player to place or refresh in the queue.
        """

    async def try_match(self, player_id: UUID) -> PairRecord | None:
        """Attempt transactional matchmaking with SKIP LOCKED and TTL eviction."""

    async def get_active_pair(self, player_id: UUID) -> PairRecord | None:
        """Return the player's active pair if any."""

    async def create_turn(
        self,
        *,
        pair_id: UUID,
        speaker_id: UUID,
        guesser_id: UUID,
        card_id: UUID,
    ) -> TurnRecord:
        """Insert a new ``awaiting_audio`` turn."""

    async def pick_card_for_pair(self, pair_id: UUID) -> CardRecord | None:
        """Choose a verified live-deck card, preferring unused cards."""

    async def get_turn(self, turn_id: UUID) -> TurnRecord | None:
        """Fetch a turn by id."""

    async def get_latest_turn(self, pair_id: UUID) -> TurnRecord | None:
        """Fetch the newest turn for a pair."""

    async def get_card(self, card_id: UUID) -> CardRecord | None:
        """Fetch a card by id."""

    async def get_cards(self, card_ids: list[UUID]) -> list[CardRecord]:
        """Fetch many cards by id."""

    async def accept_audio(
        self,
        *,
        turn_id: UUID,
        audio_path: str,
        duration_s: float,
    ) -> TurnRecord:
        """Persist accepted audio and move turn to ``awaiting_label_confirm``."""

    async def enqueue_job(self, *, kind: str, turn_id: UUID) -> bool:
        """Idempotently insert a job; return True when a new row was created."""

    async def confirm_label(self, turn_id: UUID) -> TurnRecord:
        """Move turn from ``awaiting_label_confirm`` to ``awaiting_guess``."""

    async def apply_guess(
        self,
        *,
        turn_id: UUID,
        correct: bool,
    ) -> TurnRecord:
        """Apply one guess attempt and possibly score the turn."""

    async def set_turn_quality_for_tests(
        self,
        turn_id: UUID,
        quality: dict[str, Any],
    ) -> TurnRecord:
        """Test helper to attach machine quality without the worker."""

    async def complete_pair_if_capped(
        self,
        *,
        pair_id: UUID,
        rounds_cap: int,
    ) -> bool:
        """Mark pair completed when either player reached the session cap."""

    async def fetch_state_bundle(self, player_id: UUID, *, leaderboard_top: int) -> StateBundle:
        """Load all facts needed for ``/api/state`` in one round trip."""

    async def player_stats(self, player_id: UUID) -> PlayerStats:
        """Compute score, rounds played, and rank for a player."""

    async def leaderboard(self, *, top: int) -> list[LeaderboardRow]:
        """Return top nicknames by validated-pair points."""

    async def metrics(self) -> MetricsSnapshot:
        """Return venue throughput metrics."""

    async def count_jobs(self, *, kind: str, turn_id: UUID) -> int:
        """Count jobs for a turn/kind (tests and diagnostics)."""

    async def seed_deck(
        self,
        *,
        region_tag: str,
        cards: list[dict[str, Any]],
    ) -> UUID:
        """Insert a live deck and cards for isolated tests."""
