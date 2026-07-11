"""In-memory GameStore for isolated game-core tests.

Implements the same behavioral contracts as the Postgres adapter—including
matchmaking exclusion, turn transitions, and idempotent job insert—without
opening a database connection or mutating runtime data under ``./data``.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from app.game.config import get_game_config
from app.game.types import (
    CardRecord,
    LeaderboardRow,
    MetricsSnapshot,
    PairRecord,
    PlayerRecord,
    PlayerStats,
    StateBundle,
    TurnRecord,
    resolve_label_text,
    shared_languages,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp.

    Returns:
        Current UTC ``datetime``.
    """
    return datetime.now(timezone.utc)


class MemoryGameStore:
    """Process-local durable stand-in for Postgres game tables."""

    def __init__(self) -> None:
        """Initialize empty collections for players, pairs, turns, and jobs."""
        logger.info("MemoryGameStore.__init__ called")
        self.players: dict[UUID, PlayerRecord] = {}
        self.players_by_hash: dict[str, UUID] = {}
        self.queue: dict[UUID, datetime] = {}
        self.pairs: dict[UUID, PairRecord] = {}
        self.decks: dict[UUID, dict[str, Any]] = {}
        self.cards: dict[UUID, CardRecord] = {}
        self.turns: dict[UUID, TurnRecord] = {}
        self.jobs: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []
        self.metrics_counters: dict[str, int] = {
            "validated_pairs": 0,
            "training_eligible_pairs": 0,
        }

    async def create_player(
        self,
        *,
        nickname: str,
        native_lang: str,
        common_langs: list[str],
        session_token_hash: str,
    ) -> PlayerRecord:
        """Insert a player into the in-memory tables.

        Args:
            nickname: Display name.
            native_lang: Declared native language.
            common_langs: Shared languages for matchmaking.
            session_token_hash: SHA-256 hex of the bearer token.

        Returns:
            The created ``PlayerRecord``.
        """
        logger.info(
            "MemoryGameStore.create_player called nickname_len=%s native_lang=%s "
            "common_count=%s",
            len(nickname),
            native_lang,
            len(common_langs),
        )
        player = PlayerRecord(
            id=uuid4(),
            nickname=nickname,
            native_lang=native_lang,
            common_langs=list(common_langs),
            session_token_hash=session_token_hash,
            created_at=_utcnow(),
        )
        self.players[player.id] = player
        self.players_by_hash[session_token_hash] = player.id
        return player

    async def get_player_by_token_hash(self, token_hash: str) -> PlayerRecord | None:
        """Lookup a player by hashed bearer token.

        Args:
            token_hash: SHA-256 hex digest.

        Returns:
            Matching player or ``None``.
        """
        logger.info(
            "MemoryGameStore.get_player_by_token_hash called hash_prefix=%s",
            token_hash[:8],
        )
        player_id = self.players_by_hash.get(token_hash)
        if player_id is None:
            return None
        return self.players[player_id]

    async def get_player(self, player_id: UUID) -> PlayerRecord | None:
        """Lookup a player by id.

        Args:
            player_id: Player UUID.

        Returns:
            Matching player or ``None``.
        """
        logger.info("MemoryGameStore.get_player called player_id=%s", player_id)
        return self.players.get(player_id)

    async def enqueue_player(self, player_id: UUID) -> None:
        """Idempotently enqueue a player for matchmaking.

        Args:
            player_id: Player to place in the queue.
        """
        logger.info("MemoryGameStore.enqueue_player called player_id=%s", player_id)
        if player_id not in self.queue:
            self.queue[player_id] = _utcnow()

    async def try_match(self, player_id: UUID) -> PairRecord | None:
        """Match the player with a compatible queued partner.

        Args:
            player_id: Player requesting a match.

        Returns:
            New ``PairRecord`` when matched, otherwise ``None``.

        Side effects:
            Removes both players from the queue and creates the first turn when
            a live card is available.
        """
        logger.info("MemoryGameStore.try_match called player_id=%s", player_id)
        existing = await self.get_active_pair(player_id)
        if existing is not None:
            return existing
        me = self.players[player_id]
        if player_id not in self.queue:
            self.queue[player_id] = _utcnow()

        candidates = [
            pid
            for pid in list(self.queue)
            if pid != player_id
        ]
        # Stable order by enqueue time to mimic SKIP LOCKED claim order.
        candidates.sort(key=lambda pid: self.queue[pid])
        for other_id in candidates:
            other = self.players[other_id]
            if other.native_lang == me.native_lang:
                continue
            shared = shared_languages(me.common_langs, other.common_langs)
            if not shared:
                continue
            if self._previously_paired(player_id, other_id) and self._has_alternate(
                player_id,
                me,
                exclude={other_id},
            ):
                continue
            common_lang = shared[0]
            # Deterministic player_a / player_b by enqueue order.
            if self.queue[player_id] <= self.queue[other_id]:
                player_a, player_b = player_id, other_id
            else:
                player_a, player_b = other_id, player_id
            pair = PairRecord(
                id=uuid4(),
                player_a=player_a,
                player_b=player_b,
                common_lang=common_lang,
                status="active",
                created_at=_utcnow(),
            )
            self.pairs[pair.id] = pair
            self.queue.pop(player_id, None)
            self.queue.pop(other_id, None)
            card = await self.pick_card_for_pair(pair.id)
            if card is not None:
                await self.create_turn(
                    pair_id=pair.id,
                    speaker_id=player_a,
                    guesser_id=player_b,
                    card_id=card.id,
                )
            logger.info(
                "MemoryGameStore.try_match matched pair_id=%s common_lang=%s",
                pair.id,
                common_lang,
            )
            return pair
        logger.info("MemoryGameStore.try_match no_partner player_id=%s", player_id)
        return None

    def _previously_paired(self, a: UUID, b: UUID) -> bool:
        """Return whether two players already share any historical pair."""
        for pair in self.pairs.values():
            members = {pair.player_a, pair.player_b}
            if a in members and b in members:
                return True
        return False

    def _has_alternate(
        self,
        player_id: UUID,
        me: PlayerRecord,
        *,
        exclude: set[UUID],
    ) -> bool:
        """Return whether another compatible queued partner exists."""
        for other_id, _enqueued in self.queue.items():
            if other_id == player_id or other_id in exclude:
                continue
            other = self.players[other_id]
            if other.native_lang == me.native_lang:
                continue
            if shared_languages(me.common_langs, other.common_langs):
                return True
        return False

    async def get_active_pair(self, player_id: UUID) -> PairRecord | None:
        """Return the player's active pair if present.

        Args:
            player_id: Player UUID.

        Returns:
            Active pair or ``None``.
        """
        logger.info("MemoryGameStore.get_active_pair called player_id=%s", player_id)
        matches = [
            pair
            for pair in self.pairs.values()
            if pair.status == "active"
            and player_id in (pair.player_a, pair.player_b)
        ]
        if not matches:
            return None
        matches.sort(key=lambda p: p.created_at, reverse=True)
        return matches[0]

    async def create_turn(
        self,
        *,
        pair_id: UUID,
        speaker_id: UUID,
        guesser_id: UUID,
        card_id: UUID,
    ) -> TurnRecord:
        """Insert a new awaiting-audio turn.

        Args:
            pair_id: Owning pair.
            speaker_id: Player who will record.
            guesser_id: Player who will guess.
            card_id: Card being described.

        Returns:
            The created turn.
        """
        logger.info(
            "MemoryGameStore.create_turn called pair_id=%s speaker_id=%s "
            "guesser_id=%s card_id=%s",
            pair_id,
            speaker_id,
            guesser_id,
            card_id,
        )
        turn = TurnRecord(
            id=uuid4(),
            pair_id=pair_id,
            speaker_id=speaker_id,
            guesser_id=guesser_id,
            card_id=card_id,
            status="awaiting_audio",
            audio_path=None,
            audio_flac_path=None,
            duration_s=None,
            quality=None,
            attempts=0,
            outcome="pending",
            created_at=_utcnow(),
        )
        self.turns[turn.id] = turn
        return turn

    async def pick_card_for_pair(self, pair_id: UUID) -> CardRecord | None:
        """Choose a verified card from a live deck, preferring unused ones.

        Args:
            pair_id: Pair that will play the card.

        Returns:
            A card or ``None`` when no live verified cards exist.
        """
        logger.info("MemoryGameStore.pick_card_for_pair called pair_id=%s", pair_id)
        used = {
            turn.card_id
            for turn in self.turns.values()
            if turn.pair_id == pair_id
        }
        live_deck_ids = {
            deck_id
            for deck_id, deck in self.decks.items()
            if deck.get("status") == "live"
        }
        candidates = [
            card
            for card in self.cards.values()
            if card.verified and card.deck_id in live_deck_ids
        ]
        if not candidates:
            return None
        unused = [card for card in candidates if card.id not in used]
        pool = unused or candidates
        return random.choice(pool)

    async def get_turn(self, turn_id: UUID) -> TurnRecord | None:
        """Fetch a turn by id."""
        logger.info("MemoryGameStore.get_turn called turn_id=%s", turn_id)
        return self.turns.get(turn_id)

    async def get_latest_turn(self, pair_id: UUID) -> TurnRecord | None:
        """Fetch the newest turn for a pair."""
        logger.info("MemoryGameStore.get_latest_turn called pair_id=%s", pair_id)
        turns = [t for t in self.turns.values() if t.pair_id == pair_id]
        if not turns:
            return None
        # Prefer the one active turn. Windows clock resolution can give the
        # newly created turn the same timestamp as the scored predecessor, so
        # random UUID ordering alone is not a safe recency signal.
        turns.sort(
            key=lambda t: (t.status != "scored", t.created_at, t.id),
            reverse=True,
        )
        return turns[0]

    async def get_card(self, card_id: UUID) -> CardRecord | None:
        """Fetch a card by id."""
        logger.info("MemoryGameStore.get_card called card_id=%s", card_id)
        return self.cards.get(card_id)

    async def get_cards(self, card_ids: list[UUID]) -> list[CardRecord]:
        """Fetch many cards by id."""
        logger.info("MemoryGameStore.get_cards called count=%s", len(card_ids))
        return [self.cards[cid] for cid in card_ids if cid in self.cards]

    async def accept_audio(
        self,
        *,
        turn_id: UUID,
        audio_path: str,
        duration_s: float,
    ) -> TurnRecord:
        """Persist accepted audio and advance to label confirmation.

        Args:
            turn_id: Turn receiving audio.
            audio_path: Path relative to ``DATA_DIR``.
            duration_s: Measured duration.

        Returns:
            Updated turn.

        Raises:
            ValueError: If the turn is missing or not awaiting audio.
        """
        logger.info(
            "MemoryGameStore.accept_audio called turn_id=%s duration_s=%s "
            "path_len=%s",
            turn_id,
            duration_s,
            len(audio_path),
        )
        turn = self.turns[turn_id]
        if turn.status != "awaiting_audio":
            raise ValueError("turn not awaiting audio")
        turn.audio_path = audio_path
        turn.duration_s = duration_s
        turn.status = "awaiting_label_confirm"
        return turn

    async def enqueue_job(self, *, kind: str, turn_id: UUID) -> bool:
        """Idempotently insert a triage/package job.

        Args:
            kind: ``triage`` or ``package``.
            turn_id: Target turn UUID.

        Returns:
            ``True`` when a new job row was created.
        """
        logger.info(
            "MemoryGameStore.enqueue_job called kind=%s turn_id=%s",
            kind,
            turn_id,
        )
        for job in self.jobs:
            if job["kind"] == kind and job["payload"].get("turn_id") == str(turn_id):
                return False
        self.jobs.append(
            {
                "id": uuid4(),
                "kind": kind,
                "payload": {"turn_id": str(turn_id)},
                "status": "pending",
                "tries": 0,
                "created_at": _utcnow(),
            }
        )
        return True

    async def confirm_label(self, turn_id: UUID) -> TurnRecord:
        """Advance turn to awaiting guess after label confirmation."""
        logger.info("MemoryGameStore.confirm_label called turn_id=%s", turn_id)
        turn = self.turns[turn_id]
        if turn.status != "awaiting_label_confirm":
            raise ValueError("turn not awaiting label confirm")
        turn.status = "awaiting_guess"
        return turn

    async def apply_guess(self, *, turn_id: UUID, correct: bool) -> TurnRecord:
        """Apply a guess attempt and score when appropriate.

        Args:
            turn_id: Active guess turn.
            correct: Whether the selected option matches the card.

        Returns:
            Updated turn after attempt accounting.
        """
        logger.info(
            "MemoryGameStore.apply_guess called turn_id=%s correct=%s",
            turn_id,
            correct,
        )
        cfg = get_game_config()
        turn = self.turns[turn_id]
        if turn.status != "awaiting_guess":
            raise ValueError("turn not awaiting guess")
        turn.attempts += 1
        if correct:
            turn.status = "scored"
            turn.outcome = "validated"
            self.metrics_counters["validated_pairs"] = (
                self.metrics_counters.get("validated_pairs", 0) + 1
            )
        elif turn.attempts >= cfg.max_guess_attempts:
            turn.status = "scored"
            turn.outcome = "unclear"
        return turn

    async def set_turn_quality_for_tests(
        self,
        turn_id: UUID,
        quality: dict[str, Any],
    ) -> TurnRecord:
        """Attach machine quality metadata for package-enqueue tests.

        Args:
            turn_id: Target turn.
            quality: Quality JSON object.

        Returns:
            Updated turn.
        """
        logger.info(
            "MemoryGameStore.set_turn_quality_for_tests called turn_id=%s",
            turn_id,
        )
        turn = self.turns[turn_id]
        turn.quality = dict(quality)
        return turn

    async def complete_pair_if_capped(self, *, pair_id: UUID, rounds_cap: int) -> bool:
        """Mark the pair completed when either player hit the round cap.

        Args:
            pair_id: Pair to evaluate.
            rounds_cap: Maximum scored rounds per player session.

        Returns:
            ``True`` when the pair was marked completed.
        """
        logger.info(
            "MemoryGameStore.complete_pair_if_capped called pair_id=%s rounds_cap=%s",
            pair_id,
            rounds_cap,
        )
        pair = self.pairs[pair_id]
        for player_id in (pair.player_a, pair.player_b):
            stats = await self.player_stats(player_id)
            if stats.rounds_played >= rounds_cap:
                pair.status = "completed"
                return True
        return False

    async def fetch_state_bundle(
        self,
        player_id: UUID,
        *,
        leaderboard_top: int,
    ) -> StateBundle:
        """Assemble state facts equivalent to the one-round-trip SQL query.

        Args:
            player_id: Authenticated player.
            leaderboard_top: Number of leaderboard rows to embed.

        Returns:
            ``StateBundle`` for phase composition.
        """
        logger.info(
            "MemoryGameStore.fetch_state_bundle called player_id=%s top=%s",
            player_id,
            leaderboard_top,
        )
        player = self.players[player_id]
        queued = player_id in self.queue
        pair = await self.get_active_pair(player_id)
        partner = None
        turn = None
        card = None
        previous_scored = None
        decoy_labels: dict[str, str] = {}
        if pair is not None:
            partner_id = pair.player_b if pair.player_a == player_id else pair.player_a
            partner = self.players[partner_id]
            turn = await self.get_latest_turn(pair.id)
            if turn is not None:
                card = self.cards.get(turn.card_id)
                if card is not None:
                    decoy_ids = [UUID(x) for x in card.decoys]
                    for decoy in await self.get_cards(decoy_ids):
                        decoy_labels[str(decoy.id)] = resolve_label_text(
                            decoy.label_common,
                            pair.common_lang,
                        )
            scored = [
                t
                for t in self.turns.values()
                if t.pair_id == pair.id and t.status == "scored"
            ]
            scored.sort(key=lambda t: (t.created_at, t.id), reverse=True)
            previous_scored = scored[0] if scored else None
        stats = await self.player_stats(player_id)
        board = await self.leaderboard(top=leaderboard_top)
        return StateBundle(
            player=player,
            queued=queued,
            pair=pair,
            partner=partner,
            turn=turn,
            card=card,
            previous_scored=previous_scored,
            stats=stats,
            leaderboard_top=board,
            decoy_labels=decoy_labels,
        )

    async def player_stats(self, player_id: UUID) -> PlayerStats:
        """Compute validated points, rounds played, and leaderboard rank."""
        logger.info("MemoryGameStore.player_stats called player_id=%s", player_id)
        cfg = get_game_config()
        rounds_played = 0
        validated = 0
        for turn in self.turns.values():
            if player_id not in (turn.speaker_id, turn.guesser_id):
                continue
            if turn.status == "scored":
                rounds_played += 1
            if turn.outcome == "validated":
                validated += 1
        score = validated * cfg.points_per_validation
        board = await self.leaderboard(top=10_000)
        rank = None
        me = self.players[player_id]
        for index, row in enumerate(board, start=1):
            if row.nickname == me.nickname and row.score == score:
                rank = index
                break
        return PlayerStats(score=score, rounds_played=rounds_played, rank=rank)

    async def leaderboard(self, *, top: int) -> list[LeaderboardRow]:
        """Return top nicknames by validated-pair points."""
        logger.info("MemoryGameStore.leaderboard called top=%s", top)
        cfg = get_game_config()
        scores: dict[UUID, int] = {pid: 0 for pid in self.players}
        for turn in self.turns.values():
            if turn.outcome != "validated":
                continue
            scores[turn.speaker_id] = scores.get(turn.speaker_id, 0) + cfg.points_per_validation
            scores[turn.guesser_id] = scores.get(turn.guesser_id, 0) + cfg.points_per_validation
        rows = [
            LeaderboardRow(nickname=self.players[pid].nickname, score=score)
            for pid, score in scores.items()
            if score > 0 or True
        ]
        rows.sort(key=lambda r: (-r.score, r.nickname))
        return rows[:top]

    async def metrics(self) -> MetricsSnapshot:
        """Return venue throughput metrics from counters and player langs."""
        logger.info("MemoryGameStore.metrics called")
        languages: set[str] = set()
        for player in self.players.values():
            languages.add(player.native_lang)
            languages.update(player.common_langs)
        validated = sum(1 for t in self.turns.values() if t.outcome == "validated")
        eligible = self.metrics_counters.get("training_eligible_pairs", 0)
        return MetricsSnapshot(
            validated_pairs=validated,
            training_eligible_pairs=eligible,
            language_count=len(languages),
            languages=sorted(languages),
        )

    async def count_jobs(self, *, kind: str, turn_id: UUID) -> int:
        """Count jobs for diagnostics and smoke assertions."""
        logger.info(
            "MemoryGameStore.count_jobs called kind=%s turn_id=%s",
            kind,
            turn_id,
        )
        return sum(
            1
            for job in self.jobs
            if job["kind"] == kind and job["payload"].get("turn_id") == str(turn_id)
        )

    async def seed_deck(
        self,
        *,
        region_tag: str,
        cards: list[dict[str, Any]],
    ) -> UUID:
        """Insert a live deck and cards for isolated tests.

        Args:
            region_tag: Deck region label.
            cards: Card dicts with ``image_path``, ``label_common``, ``decoys``.

        Returns:
            Created deck UUID.
        """
        logger.info(
            "MemoryGameStore.seed_deck called region_tag=%s card_count=%s",
            region_tag,
            len(cards),
        )
        deck_id = uuid4()
        self.decks[deck_id] = {
            "id": deck_id,
            "region_tag": region_tag,
            "status": "live",
        }
        created_ids: list[UUID] = []
        pending: list[tuple[UUID, dict[str, Any]]] = []
        for raw in cards:
            card_id = uuid4()
            created_ids.append(card_id)
            pending.append((card_id, raw))
        # Resolve decoys as other card ids when caller passes indices or omit.
        for index, (card_id, raw) in enumerate(pending):
            decoys_raw = raw.get("decoys")
            if decoys_raw is None:
                decoy_ids = [str(cid) for i, cid in enumerate(created_ids) if i != index][:5]
            else:
                decoy_ids = [str(x) for x in decoys_raw]
            self.cards[card_id] = CardRecord(
                id=card_id,
                deck_id=deck_id,
                image_path=str(raw["image_path"]),
                label_common=dict(raw["label_common"]),
                decoys=decoy_ids,
                verified=True,
            )
        return deck_id
