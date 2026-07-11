"""Compose player-facing ``StateResponse`` from a store state bundle.

Enforces label visibility rules: the semantic label is never returned before
audio is accepted. Maps internal turn statuses onto ``contracts.api_types.Phase``
values and computes ``state_version`` for cheap client poll short-circuiting.
"""

from __future__ import annotations

import logging
import random
import zlib
from datetime import datetime, timezone
from uuid import UUID

from contracts.api_types import (
    LabelState,
    LeaderboardEntry,
    OptionState,
    PairState,
    Phase,
    PlayerState,
    RoundResult,
    StateResponse,
    TurnState,
)

from app.game.config import GameFeatureConfig, get_game_config
from app.game.types import LeaderboardRow, StateBundle, resolve_label_text

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return timezone-aware UTC now.

    Returns:
        Current UTC datetime.
    """
    return datetime.now(timezone.utc)


def compute_state_version(
    *,
    phase: Phase,
    player_id: UUID,
    pair_id: UUID | None,
    turn_id: UUID | None,
    turn_status: str | None,
    attempts: int,
    score: int,
    rank: int | None,
    rounds_played: int,
    leaderboard_top: list[LeaderboardRow],
    queued: bool,
) -> int:
    """Derive a stable integer version for poll short-circuiting.

    Args:
        phase: Composed player phase.
        player_id: Authenticated player.
        pair_id: Active pair id when paired.
        turn_id: Latest turn id when present.
        turn_status: Latest turn status when present.
        attempts: Guess attempts on the latest turn.
        score: Player score.
        rank: Current leaderboard rank.
        rounds_played: Number of scored rounds for the player.
        leaderboard_top: Current leaderboard rows included in the response.
        queued: Whether the player is waiting in matchmaking.

    Returns:
        Non-negative 31-bit integer CRC of the state fingerprint.
    """
    logger.info(
        "compute_state_version called phase=%s player_id=%s pair_id=%s "
        "turn_id=%s turn_status=%s attempts=%s score=%s rank=%s "
        "rounds_played=%s leaderboard_count=%s queued=%s",
        phase,
        player_id,
        pair_id,
        turn_id,
        turn_status,
        attempts,
        score,
        rank,
        rounds_played,
        len(leaderboard_top),
        queued,
    )
    leaderboard_fingerprint = ",".join(
        f"{row.nickname}:{row.score}" for row in leaderboard_top
    )
    payload = "|".join(
        [
            phase.value,
            str(player_id),
            str(pair_id or ""),
            str(turn_id or ""),
            turn_status or "",
            str(attempts),
            str(score),
            str(rank or ""),
            str(rounds_played),
            leaderboard_fingerprint,
            "1" if queued else "0",
        ]
    )
    return zlib.crc32(payload.encode("utf-8")) & 0x7FFFFFFF


def compose_state_response(
    bundle: StateBundle,
    *,
    rounds_cap: int,
    config: GameFeatureConfig | None = None,
) -> StateResponse:
    """Build the contract ``StateResponse`` for one player.

    Args:
        bundle: Facts from ``GameStore.fetch_state_bundle``.
        rounds_cap: Maximum scored rounds per player session.
        config: Optional feature config override.

    Returns:
        Fully populated ``StateResponse`` with visibility rules applied.

    Side effects:
        Logs composition metadata; never logs label text or audio payloads.
    """
    cfg = config or get_game_config()
    logger.info(
        "compose_state_response called player_id=%s rounds_cap=%s queued=%s "
        "has_pair=%s has_turn=%s",
        bundle.player.id,
        rounds_cap,
        bundle.queued,
        bundle.pair is not None,
        bundle.turn is not None,
    )

    stats = bundle.stats
    if stats.rounds_played >= rounds_cap or (
        bundle.pair is not None and bundle.pair.status == "completed"
    ):
        phase = Phase.SESSION_DONE
        response = StateResponse(
            state_version=compute_state_version(
                phase=phase,
                player_id=bundle.player.id,
                pair_id=bundle.pair.id if bundle.pair else None,
                turn_id=bundle.turn.id if bundle.turn else None,
                turn_status=bundle.turn.status if bundle.turn else None,
                attempts=bundle.turn.attempts if bundle.turn else 0,
                score=stats.score,
                rank=stats.rank,
                rounds_played=stats.rounds_played,
                leaderboard_top=bundle.leaderboard_top,
                queued=False,
            ),
            phase=phase,
            player=PlayerState(
                nickname=bundle.player.nickname,
                score=stats.score,
                rank=stats.rank,
                rounds_played=stats.rounds_played,
                rounds_cap=rounds_cap,
            ),
            pair=_pair_state(bundle),
            turn=None,
            last_result=_last_result(bundle, cfg),
            leaderboard_top=_board(bundle),
        )
        logger.info("compose_state_response completed phase=%s", phase)
        return response

    if bundle.pair is None:
        phase = Phase.QUEUED if bundle.queued else Phase.ONBOARDING
        response = StateResponse(
            state_version=compute_state_version(
                phase=phase,
                player_id=bundle.player.id,
                pair_id=None,
                turn_id=None,
                turn_status=None,
                attempts=0,
                score=stats.score,
                rank=stats.rank,
                rounds_played=stats.rounds_played,
                leaderboard_top=bundle.leaderboard_top,
                queued=bundle.queued,
            ),
            phase=phase,
            player=PlayerState(
                nickname=bundle.player.nickname,
                score=stats.score,
                rank=stats.rank,
                rounds_played=stats.rounds_played,
                rounds_cap=rounds_cap,
            ),
            pair=None,
            turn=None,
            last_result=None,
            leaderboard_top=_board(bundle),
        )
        logger.info("compose_state_response completed phase=%s", phase)
        return response

    turn = bundle.turn
    card = bundle.card
    role = None
    if turn is not None:
        if turn.speaker_id == bundle.player.id:
            role = "speaker"
        elif turn.guesser_id == bundle.player.id:
            role = "guesser"

    phase = _phase_for_turn(bundle, role=role, config=cfg)
    turn_state = _turn_state(
        bundle,
        role=role,
        phase=phase,
        config=cfg,
    )
    response = StateResponse(
        state_version=compute_state_version(
            phase=phase,
            player_id=bundle.player.id,
            pair_id=bundle.pair.id,
            turn_id=turn.id if turn else None,
            turn_status=turn.status if turn else None,
            attempts=turn.attempts if turn else 0,
            score=stats.score,
            rank=stats.rank,
            rounds_played=stats.rounds_played,
            leaderboard_top=bundle.leaderboard_top,
            queued=False,
        ),
        phase=phase,
        player=PlayerState(
            nickname=bundle.player.nickname,
            score=stats.score,
            rank=stats.rank,
            rounds_played=stats.rounds_played,
            rounds_cap=rounds_cap,
        ),
        pair=_pair_state(bundle),
        turn=turn_state,
        last_result=_last_result(bundle, cfg) if phase == Phase.ROUND_RESULT else None,
        leaderboard_top=_board(bundle),
    )

    # Label leak hard guard: speaking_view_image must never carry label text.
    if phase == Phase.SPEAKING_VIEW_IMAGE and response.turn is not None:
        response.turn.label = None

    logger.info(
        "compose_state_response completed phase=%s role=%s has_label=%s "
        "has_options=%s",
        phase,
        role,
        bool(response.turn and response.turn.label),
        bool(response.turn and response.turn.options),
    )
    _ = card
    return response


def _pair_state(bundle: StateBundle) -> PairState | None:
    """Build pair view state when a partner exists.

    Args:
        bundle: State facts.

    Returns:
        ``PairState`` or ``None``.
    """
    if bundle.pair is None or bundle.partner is None:
        return None
    return PairState(
        partner_nickname=bundle.partner.nickname,
        common_lang=bundle.pair.common_lang,
    )


def _board(bundle: StateBundle) -> list[LeaderboardEntry]:
    """Map leaderboard rows to contract entries."""
    return [
        LeaderboardEntry(nickname=row.nickname, score=row.score)
        for row in bundle.leaderboard_top
    ]


def _last_result(bundle: StateBundle, config: GameFeatureConfig) -> RoundResult | None:
    """Build round result from the most recent scored turn.

    Args:
        bundle: State facts.
        config: Feature config for points delta.

    Returns:
        ``RoundResult`` when a scored turn exists, else ``None``.
    """
    scored = bundle.previous_scored
    if scored is None:
        return None
    if scored.outcome == "validated":
        return RoundResult(
            outcome="validated",
            points_delta=config.points_per_validation,
            message="Nice! That pair is validated.",
        )
    if scored.outcome == "unclear":
        return RoundResult(
            outcome="unclear",
            points_delta=0,
            message="No points this round — still useful for research.",
        )
    return RoundResult(
        outcome=scored.outcome,
        points_delta=0,
        message="Round complete.",
    )


def _phase_for_turn(
    bundle: StateBundle,
    *,
    role: str | None,
    config: GameFeatureConfig,
) -> Phase:
    """Map turn status and role onto a client phase.

    Args:
        bundle: State facts including latest and previous turns.
        role: ``speaker``, ``guesser``, or ``None``.
        config: Feature config for result-hold timing.

    Returns:
        Contract ``Phase`` value.
    """
    turn = bundle.turn
    if turn is None:
        return Phase.WAITING_PARTNER

    # Hold round_result briefly after the next turn is created.
    if (
        turn.status == "awaiting_audio"
        and bundle.previous_scored is not None
        and bundle.previous_scored.id != turn.id
    ):
        age = (_utcnow() - turn.created_at).total_seconds()
        if age < config.result_hold_seconds:
            return Phase.ROUND_RESULT

    if turn.status == "scored":
        return Phase.ROUND_RESULT

    if turn.status == "awaiting_audio":
        return Phase.SPEAKING_VIEW_IMAGE if role == "speaker" else Phase.WAITING_PARTNER
    if turn.status == "awaiting_label_confirm":
        return (
            Phase.SPEAKING_CONFIRM_LABEL if role == "speaker" else Phase.WAITING_PARTNER
        )
    if turn.status == "awaiting_guess":
        return Phase.GUESSING if role == "guesser" else Phase.WAITING_PARTNER
    return Phase.WAITING_PARTNER


def _turn_state(
    bundle: StateBundle,
    *,
    role: str | None,
    phase: Phase,
    config: GameFeatureConfig,
) -> TurnState | None:
    """Build role-scoped turn payload with visibility rules.

    Args:
        bundle: State facts.
        role: Player role on the latest turn.
        phase: Already-selected phase.
        config: Feature config for deadlines and attempts.

    Returns:
        ``TurnState`` or ``None`` when no turn is active.
    """
    turn = bundle.turn
    card = bundle.card
    pair = bundle.pair
    if turn is None or pair is None:
        return None

    deadline_ts = None
    if config.turn_deadline_seconds is not None:
        deadline_ts = int(turn.created_at.timestamp()) + config.turn_deadline_seconds

    image_url = None
    label = None
    options = None
    audio_url = None
    attempts_left = None

    if phase == Phase.ROUND_RESULT:
        return TurnState(
            role=role,
            card_image_url=None,
            label=None,
            options=None,
            audio_url=None,
            attempts_left=None,
            deadline_ts=deadline_ts,
        )

    if role == "speaker" and card is not None:
        image_url = f"/media/{card.image_path.replace(chr(92), '/')}"
        # Label is scoped to the explicit confirm phase and never retained in
        # waiting/result payloads.
        if phase == Phase.SPEAKING_CONFIRM_LABEL:
            text = resolve_label_text(card.label_common, pair.common_lang)
            label = LabelState(text=text)

    if role == "guesser" and turn.status == "awaiting_guess" and card is not None:
        if turn.audio_path:
            audio_url = f"/media/{turn.audio_path.replace(chr(92), '/')}"
        correct_text = resolve_label_text(card.label_common, pair.common_lang)
        option_items = [OptionState(id=card.id, text=correct_text)]
        for decoy_id, decoy_text in bundle.decoy_labels.items():
            option_items.append(OptionState(id=UUID(decoy_id), text=decoy_text))
        # Stable shuffle per turn so both polls agree within a process restart
        # window; seeded by turn id.
        rng = random.Random(str(turn.id))
        rng.shuffle(option_items)
        options = option_items
        attempts_left = max(0, config.max_guess_attempts - turn.attempts)

    # Guesser waiting still may need no turn details beyond role.
    if role == "guesser" and turn.status != "awaiting_guess":
        return TurnState(role=role, deadline_ts=deadline_ts)

    return TurnState(
        role=role,
        card_image_url=image_url,
        label=label,
        options=options,
        audio_url=audio_url,
        attempts_left=attempts_left,
        deadline_ts=deadline_ts,
    )
