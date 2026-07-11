"""Turn action endpoints: audio upload, label confirm, and guessing."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, UploadFile

from app.api.deps import get_current_player, get_game_service, raise_game_error
from app.game.service import GameError, GameService
from app.game.types import PlayerRecord
from contracts.api_types import AudioUploadResponse, GuessRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["game"])


@router.post("/api/turn/audio", response_model=AudioUploadResponse)
async def upload_audio(
    player: PlayerRecord = Depends(get_current_player),
    service: GameService = Depends(get_game_service),
    file: UploadFile = File(...),
) -> AudioUploadResponse:
    """Accept multipart audio for the current speaker turn.

    Args:
        player: Authenticated speaker.
        service: Injected game service.
        file: Multipart file field (browser webm/mp4).

    Returns:
        ``ok`` when accepted and triage-queued, or ``re_record`` with a playful
        reason after fast duration/silence checks.

    Side effects:
        Writes a server-named file under ``data/audio/`` and may enqueue
        ``triage`` with payload ``{"turn_id": "<uuid>"}``.
    """
    logger.info(
        "upload_audio called player_id=%s content_type=%s",
        player.id,
        file.content_type,
    )
    payload = await file.read()
    try:
        response = await service.upload_audio(
            player,
            payload=payload,
            filename=file.filename,
        )
    except GameError as exc:
        raise_game_error(exc)
    logger.info(
        "upload_audio completed player_id=%s status=%s byte_length=%s",
        player.id,
        response.status,
        len(payload),
    )
    return response


@router.post("/api/turn/confirm-label")
async def confirm_label(
    player: PlayerRecord = Depends(get_current_player),
    service: GameService = Depends(get_game_service),
) -> dict[str, str]:
    """Confirm the revealed label after audio acceptance.

    Args:
        player: Authenticated speaker.
        service: Injected game service.

    Returns:
        ``{"status": "ok"}``.
    """
    logger.info("confirm_label called player_id=%s", player.id)
    try:
        result = await service.confirm_label(player)
    except GameError as exc:
        raise_game_error(exc)
    logger.info("confirm_label completed player_id=%s", player.id)
    return result


@router.post("/api/turn/guess")
async def guess(
    body: GuessRequest,
    player: PlayerRecord = Depends(get_current_player),
    service: GameService = Depends(get_game_service),
) -> dict[str, str]:
    """Submit a guesser option selection.

    Args:
        body: Selected ``option_id`` (card UUID).
        player: Authenticated guesser.
        service: Injected game service.

    Returns:
        ``{"status": "ok"}``; outcome is observed via ``GET /api/state``.

    Side effects:
        May score the turn, advance roles, and enqueue ``package`` when quality
        metadata already exists on the scored turn.
    """
    logger.info(
        "guess called player_id=%s option_id=%s",
        player.id,
        body.option_id,
    )
    try:
        result = await service.guess(player, option_id=body.option_id)
    except GameError as exc:
        raise_game_error(exc)
    logger.info("guess completed player_id=%s", player.id)
    return result
