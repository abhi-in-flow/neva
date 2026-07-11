"""Game-core application service orchestrating store and audio checks.

Implements join, matchmaking, turn actions, scoring, package/triage enqueue,
and state composition. API routers call this service exclusively so HTTP
concerns stay out of domain logic. No Gemini calls are made here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from contracts.api_types import (
    AudioUploadResponse,
    JoinResponse,
    LeaderboardEntry,
    LeaderboardResponse,
    MetricsResponse,
    StateResponse,
)

from app.game.audio_checks import check_audio_file
from app.game.config import REASON_TOO_LARGE, GameFeatureConfig, get_game_config
from app.game.state import compose_state_response
from app.game.store import GameStore
from app.game.tokens import hash_session_token, issue_session_token, token_fingerprint
from app.game.types import PlayerRecord, TurnRecord

logger = logging.getLogger(__name__)


class GameError(Exception):
    """Domain error with an HTTP-ish status code for API translation.

    Attributes:
        status_code: Suggested HTTP status.
        detail: Safe error detail for clients.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        """Create a domain error.

        Args:
            status_code: Suggested HTTP status code.
            detail: Client-safe error message.
        """
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class GameService:
    """High-level game operations against a ``GameStore`` backend."""

    def __init__(
        self,
        store: GameStore,
        *,
        data_dir: Path,
        rounds_cap: int,
        config: GameFeatureConfig | None = None,
        audio_checker=check_audio_file,
    ) -> None:
        """Bind store, runtime paths, and feature configuration.

        Args:
            store: Persistence adapter (Postgres or memory).
            data_dir: Runtime data root for audio paths.
            rounds_cap: Per-player scored-round session cap.
            config: Optional feature config override.
            audio_checker: Injectable audio validation function for tests.
        """
        logger.info(
            "GameService.__init__ called data_dir=%s rounds_cap=%s",
            data_dir,
            rounds_cap,
        )
        self.store = store
        self.data_dir = data_dir
        self.rounds_cap = rounds_cap
        self.config = config or get_game_config()
        self._audio_checker = audio_checker

    async def join(
        self,
        *,
        nickname: str,
        native_lang: str,
        common_langs: list[str],
    ) -> JoinResponse:
        """Register a player and issue a bearer session token.

        Args:
            nickname: Display name.
            native_lang: Declared native language.
            common_langs: Languages usable as the shared guessing language.

        Returns:
            ``JoinResponse`` containing the raw session token.

        Side effects:
            Inserts a player row with the token hash only.
        """
        logger.info(
            "GameService.join called nickname_len=%s native_lang=%s common_count=%s",
            len(nickname),
            native_lang,
            len(common_langs),
        )
        cleaned = [lang.strip() for lang in common_langs if lang and lang.strip()]
        if not cleaned:
            raise GameError(400, "common_langs must contain at least one language")
        # Native language must not be the only shared language path; still allow
        # it in the list but matchmaking requires different natives.
        token = issue_session_token()
        token_hash = hash_session_token(token)
        await self.store.create_player(
            nickname=nickname.strip(),
            native_lang=native_lang.strip(),
            common_langs=cleaned,
            session_token_hash=token_hash,
        )
        logger.info(
            "GameService.join completed token_fp=%s",
            token_fingerprint(token),
        )
        return JoinResponse(session_token=token)

    async def resolve_player(self, token: str) -> PlayerRecord:
        """Resolve a bearer token to a player record.

        Args:
            token: Raw session token from the Authorization header.

        Returns:
            Matching ``PlayerRecord``.

        Raises:
            GameError: When the token is missing or unknown.
        """
        logger.info(
            "GameService.resolve_player called token_fp=%s",
            token_fingerprint(token) if token else "empty",
        )
        if not token:
            raise GameError(401, "missing bearer token")
        player = await self.store.get_player_by_token_hash(hash_session_token(token))
        if player is None:
            raise GameError(401, "invalid session token")
        return player

    async def request_pair(self, player: PlayerRecord) -> dict[str, str]:
        """Enqueue the player and attempt transactional matchmaking.

        Args:
            player: Authenticated player.

        Returns:
            Simple status payload ``{"status": "queued"|"matched"}``.

        Side effects:
            May create a pair and the first turn when a partner is claimed.
        """
        logger.info("GameService.request_pair called player_id=%s", player.id)
        stats = await self.store.player_stats(player.id)
        if stats.rounds_played >= self.rounds_cap:
            raise GameError(409, "session round cap reached")
        existing = await self.store.get_active_pair(player.id)
        if existing is not None:
            return {"status": "matched"}
        await self.store.enqueue_player(player.id)
        pair = await self.store.try_match(player.id)
        status = "matched" if pair is not None else "queued"
        logger.info(
            "GameService.request_pair completed player_id=%s status=%s",
            player.id,
            status,
        )
        return {"status": status}

    async def get_state(self, player: PlayerRecord) -> StateResponse:
        """Compose the polling view-state for a player.

        Args:
            player: Authenticated player.

        Returns:
            Contract ``StateResponse``.
        """
        logger.info("GameService.get_state called player_id=%s", player.id)
        bundle = await self.store.fetch_state_bundle(
            player.id,
            leaderboard_top=self.config.leaderboard_state_top,
        )
        response = compose_state_response(
            bundle,
            rounds_cap=self.rounds_cap,
            config=self.config,
        )
        logger.info(
            "GameService.get_state completed player_id=%s phase=%s version=%s",
            player.id,
            response.phase,
            response.state_version,
        )
        return response

    async def upload_audio(
        self,
        player: PlayerRecord,
        *,
        payload: bytes,
        filename: str | None = None,
    ) -> AudioUploadResponse:
        """Accept multipart audio for the player's current speaker turn.

        Args:
            player: Authenticated speaker.
            payload: Raw uploaded bytes (webm/mp4/etc).
            filename: Optional client filename for extension hints only.

        Returns:
            ``AudioUploadResponse`` with ``ok`` or ``re_record``.

        Side effects:
            Writes ``data/audio/<turn_id>.webm``, updates the turn, and
            idempotently enqueues a ``triage`` job on acceptance. Rejected
            uploads delete the temporary file.
        """
        logger.info(
            "GameService.upload_audio called player_id=%s byte_length=%s "
            "filename_len=%s",
            player.id,
            len(payload),
            len(filename or ""),
        )
        if len(payload) > self.config.max_audio_bytes:
            return AudioUploadResponse(status="re_record", reason=REASON_TOO_LARGE)

        turn = await self._require_speaker_turn(player, expected_status="awaiting_audio")
        audio_dir = self.data_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        # Server-generated path only; ignore client filename for storage.
        rel_path = f"audio/{turn.id}.webm"
        abs_path = self.data_dir / rel_path
        abs_path.write_bytes(payload)

        result = self._audio_checker(
            abs_path,
            byte_length=len(payload),
            config=self.config,
        )
        if not result.accepted:
            if abs_path.exists():
                abs_path.unlink()
            logger.info(
                "GameService.upload_audio re_record turn_id=%s reason_set=%s",
                turn.id,
                bool(result.reason),
            )
            return AudioUploadResponse(status="re_record", reason=result.reason)

        await self.store.accept_audio(
            turn_id=turn.id,
            audio_path=rel_path.replace("\\", "/"),
            duration_s=float(result.duration_s or 0.0),
        )
        created = await self.store.enqueue_job(kind="triage", turn_id=turn.id)
        logger.info(
            "GameService.upload_audio accepted turn_id=%s triage_created=%s "
            "duration_s=%s",
            turn.id,
            created,
            result.duration_s,
        )
        return AudioUploadResponse(status="ok", reason=None)

    async def confirm_label(self, player: PlayerRecord) -> dict[str, str]:
        """Confirm the revealed label after accepted audio.

        Args:
            player: Authenticated speaker.

        Returns:
            ``{"status": "ok"}`` on success.
        """
        logger.info("GameService.confirm_label called player_id=%s", player.id)
        turn = await self._require_speaker_turn(
            player,
            expected_status="awaiting_label_confirm",
        )
        await self.store.confirm_label(turn.id)
        logger.info("GameService.confirm_label completed turn_id=%s", turn.id)
        return {"status": "ok"}

    async def guess(self, player: PlayerRecord, *, option_id: UUID) -> dict[str, str]:
        """Apply a guesser selection and score when the turn completes.

        Args:
            player: Authenticated guesser.
            option_id: Selected option UUID (card id).

        Returns:
            ``{"status": "ok"}``; detailed outcome arrives via ``/api/state``.

        Side effects:
            May mark the turn scored, create the next turn, complete the pair
            at the round cap, and idempotently enqueue ``package`` when quality
            metadata already exists.
        """
        logger.info(
            "GameService.guess called player_id=%s option_id=%s",
            player.id,
            option_id,
        )
        pair = await self.store.get_active_pair(player.id)
        if pair is None:
            raise GameError(409, "not paired")
        turn = await self.store.get_latest_turn(pair.id)
        if turn is None or turn.guesser_id != player.id:
            raise GameError(409, "not the guesser for this turn")
        if turn.status != "awaiting_guess":
            raise GameError(409, "turn is not awaiting a guess")

        correct = option_id == turn.card_id
        updated = await self.store.apply_guess(turn_id=turn.id, correct=correct)
        if updated.status == "scored":
            await self._after_scored(updated)
        logger.info(
            "GameService.guess completed turn_id=%s status=%s outcome=%s attempts=%s",
            updated.id,
            updated.status,
            updated.outcome,
            updated.attempts,
        )
        return {"status": "ok"}

    async def leaderboard(self, *, top: int | None = None) -> LeaderboardResponse:
        """Return the venue leaderboard.

        Args:
            top: Optional row limit; defaults to feature config.

        Returns:
            ``LeaderboardResponse`` entries ordered by score.
        """
        limit = top or self.config.leaderboard_default_top
        logger.info("GameService.leaderboard called top=%s", limit)
        rows = await self.store.leaderboard(top=limit)
        return LeaderboardResponse(
            entries=[
                LeaderboardEntry(nickname=r.nickname, score=r.score) for r in rows
            ]
        )

    async def metrics(self) -> MetricsResponse:
        """Return canonical venue throughput metrics.

        Field definitions (frozen ``MetricsResponse`` shape):

        - ``validated_pairs``: turns with ``outcome = validated``.
        - ``training_eligible_pairs``: ``records.training_eligible`` true.
        - ``languages`` / ``language_count``: normalized speaker native langs
          on validated turns only. ``common_langs`` and unplayed registrations
          are excluded by query source; no native-language tag is excluded
          because it may also be usable as a bridge language.
        - ``gauntlet_pass_rate``: eligible records ÷ packaged validated
          records; null when denominator is zero.
        - ``deck_images_per_minute`` / ``deck_cost_per_image_usd``: from the
          latest live deck ``generation_metrics``; null without evidence.
        - ``cost_per_validated_sample_usd``: latest live deck
          ``generation_metrics.total_cost_usd`` plus successful
          ``gauntlet_triage`` API costs, divided by validated pairs. It remains
          null until every validated turn is packaged and has one successful
          priced triage call; unrelated API operations are excluded.

        Returns:
            Contract ``MetricsResponse`` mapped from the store snapshot.

        Side effects:
            Delegates to the store; logs safe aggregate metadata only.
        """
        logger.info("GameService.metrics called")
        snap = await self.store.metrics()
        response = MetricsResponse(
            validated_pairs=snap.validated_pairs,
            training_eligible_pairs=snap.training_eligible_pairs,
            language_count=snap.language_count,
            languages=snap.languages,
            cost_per_validated_sample_usd=snap.cost_per_validated_sample_usd,
            gauntlet_pass_rate=snap.gauntlet_pass_rate,
            deck_images_per_minute=snap.deck_images_per_minute,
            deck_cost_per_image_usd=snap.deck_cost_per_image_usd,
        )
        logger.info(
            "GameService.metrics completed validated_pairs=%s "
            "training_eligible_pairs=%s language_count=%s "
            "gauntlet_pass_rate_present=%s cost_present=%s "
            "deck_metrics_present=%s",
            response.validated_pairs,
            response.training_eligible_pairs,
            response.language_count,
            response.gauntlet_pass_rate is not None,
            response.cost_per_validated_sample_usd is not None,
            response.deck_images_per_minute is not None
            or response.deck_cost_per_image_usd is not None,
        )
        return response

    async def _after_scored(self, turn: TurnRecord) -> None:
        """Enqueue package when possible and advance to the next turn.

        Args:
            turn: Newly scored turn.

        Side effects:
            Idempotent ``package`` insert when ``quality`` is present; creates
            the next turn with swapped roles unless the session cap is hit.
        """
        logger.info(
            "GameService._after_scored called turn_id=%s has_quality=%s",
            turn.id,
            turn.quality is not None,
        )
        # Refresh quality in case apply_guess returned a stale copy.
        fresh = await self.store.get_turn(turn.id)
        if fresh is not None and fresh.quality is not None:
            created = await self.store.enqueue_job(kind="package", turn_id=turn.id)
            logger.info(
                "GameService._after_scored package_enqueued turn_id=%s created=%s",
                turn.id,
                created,
            )

        capped = await self.store.complete_pair_if_capped(
            pair_id=turn.pair_id,
            rounds_cap=self.rounds_cap,
        )
        if capped:
            logger.info(
                "GameService._after_scored pair_completed pair_id=%s",
                turn.pair_id,
            )
            return

        # Swap roles for the next elicitation.
        card = await self.store.pick_card_for_pair(turn.pair_id)
        if card is None:
            logger.info(
                "GameService._after_scored no_card pair_id=%s",
                turn.pair_id,
            )
            return
        await self.store.create_turn(
            pair_id=turn.pair_id,
            speaker_id=turn.guesser_id,
            guesser_id=turn.speaker_id,
            card_id=card.id,
        )

    async def _require_speaker_turn(
        self,
        player: PlayerRecord,
        *,
        expected_status: str,
    ) -> TurnRecord:
        """Load the latest turn and assert the player is the speaker.

        Args:
            player: Authenticated player.
            expected_status: Required turn status.

        Returns:
            The latest turn for the active pair.

        Raises:
            GameError: On missing pair/turn or role/status mismatch.
        """
        logger.info(
            "GameService._require_speaker_turn called player_id=%s expected_status=%s",
            player.id,
            expected_status,
        )
        pair = await self.store.get_active_pair(player.id)
        if pair is None:
            raise GameError(409, "not paired")
        turn = await self.store.get_latest_turn(pair.id)
        if turn is None:
            raise GameError(409, "no active turn")
        if turn.speaker_id != player.id:
            raise GameError(409, "not the speaker for this turn")
        if turn.status != expected_status:
            raise GameError(409, f"turn is not {expected_status}")
        return turn
