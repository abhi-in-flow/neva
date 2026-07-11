"""Smoke test: two players complete three full rounds on five seeded cards.

Exercises join, matchmaking, audio acceptance, label confirm, guessing,
validated-only scoring, triage enqueue, and the no-label-before-audio rule.
Uses the in-memory store and a fake audio checker — no Gemini and no live DB.
"""

from __future__ import annotations

import logging
from uuid import UUID

from httpx import AsyncClient

from app.game.memory_store import MemoryGameStore
from app.game.service import GameService
from contracts.api_types import Phase
from tests.game.conftest import bearer

logger = logging.getLogger(__name__)

SEED_LABELS = {"fish", "tea", "bicycle", "mango", "umbrella"}


async def _join(client: AsyncClient, nickname: str, native: str, common: list[str]) -> str:
    """Join as a player and return the session token.

    Args:
        client: ASGI test client.
        nickname: Display name.
        native: Native language.
        common: Common languages.

    Returns:
        Session bearer token string.
    """
    logger.info("_join called nickname=%s native=%s", nickname, native)
    response = await client.post(
        "/api/join",
        json={"nickname": nickname, "native_lang": native, "common_langs": common},
    )
    assert response.status_code == 200, response.text
    token = response.json()["session_token"]
    assert token
    return token


async def _state(client: AsyncClient, token: str) -> dict:
    """Fetch ``/api/state`` JSON for a player.

    Args:
        client: ASGI test client.
        token: Session token.

    Returns:
        Parsed state payload.
    """
    response = await client.get("/api/state", headers=bearer(token))
    assert response.status_code == 200, response.text
    return response.json()


async def test_two_players_three_rounds_no_label_leak(
    game_client: AsyncClient,
    game_service: GameService,
    seeded_store: MemoryGameStore,
) -> None:
    """Simulate two players through three complete rounds with five cards.

    Args:
        game_client: HTTP client with memory-backed game routes.
        game_service: Underlying service for package/quality assertions.
        seeded_store: Seeded five-card store.

    Returns:
        None.

    Side effects:
        Mutates only the in-memory store and temporary audio directory.
    """
    logger.info("test_two_players_three_rounds_no_label_leak called")
    assert len(seeded_store.cards) == 5

    token_a = await _join(game_client, "Asha", "assamese", ["hindi", "english"])
    token_b = await _join(game_client, "Bala", "tamil", ["hindi", "english"])

    state_a = await _state(game_client, token_a)
    assert state_a["phase"] == Phase.ONBOARDING.value

    pair_a = await game_client.post("/api/pair/request", headers=bearer(token_a))
    assert pair_a.status_code == 200
    assert pair_a.json()["status"] == "queued"
    state_a = await _state(game_client, token_a)
    assert state_a["phase"] == Phase.QUEUED.value

    pair_b = await game_client.post("/api/pair/request", headers=bearer(token_b))
    assert pair_b.status_code == 200
    assert pair_b.json()["status"] == "matched"

    state_a = await _state(game_client, token_a)
    state_b = await _state(game_client, token_b)
    assert state_a["pair"]["common_lang"] in {"hindi", "english"}
    assert state_a["pair"]["partner_nickname"] == "Bala"
    assert state_b["pair"]["partner_nickname"] == "Asha"

    # player_a speaks first.
    speaker_token, guesser_token = token_a, token_b
    if state_a["turn"]["role"] != "speaker":
        speaker_token, guesser_token = token_b, token_a

    for round_index in range(3):
        logger.info("smoke round begin index=%s", round_index)
        speaker_state = await _state(game_client, speaker_token)
        guesser_state = await _state(game_client, guesser_token)

        assert speaker_state["phase"] == Phase.SPEAKING_VIEW_IMAGE.value
        assert speaker_state["turn"]["role"] == "speaker"
        assert speaker_state["turn"]["card_image_url"]
        assert speaker_state["turn"]["label"] is None
        # Hard no-leak: label text from the seed set must not appear anywhere.
        raw = str(speaker_state)
        for label in SEED_LABELS:
            assert f"'text': '{label}'" not in raw
            assert f'"text": "{label}"' not in raw

        assert guesser_state["phase"] == Phase.WAITING_PARTNER.value
        assert guesser_state["turn"]["label"] is None
        assert guesser_state["turn"].get("options") in (None, [])

        audio = await game_client.post(
            "/api/turn/audio",
            headers=bearer(speaker_token),
            files={"file": ("clip.webm", b"fake-webm-bytes-not-silent", "audio/webm")},
        )
        assert audio.status_code == 200, audio.text
        assert audio.json()["status"] == "ok"

        # Triage must be queued at accepted audio.
        pair = await seeded_store.get_active_pair(
            (await game_service.resolve_player(speaker_token)).id
        )
        assert pair is not None
        turn = await seeded_store.get_latest_turn(pair.id)
        assert turn is not None
        assert await seeded_store.count_jobs(kind="triage", turn_id=turn.id) == 1

        speaker_state = await _state(game_client, speaker_token)
        assert speaker_state["phase"] == Phase.SPEAKING_CONFIRM_LABEL.value
        assert speaker_state["turn"]["label"] is not None
        assert speaker_state["turn"]["label"]["text"] in SEED_LABELS

        confirm = await game_client.post(
            "/api/turn/confirm-label",
            headers=bearer(speaker_token),
        )
        assert confirm.status_code == 200

        waiting_speaker = await _state(game_client, speaker_token)
        assert waiting_speaker["phase"] == Phase.WAITING_PARTNER.value
        assert waiting_speaker["turn"]["label"] is None

        guesser_state = await _state(game_client, guesser_token)
        assert guesser_state["phase"] == Phase.GUESSING.value
        options = guesser_state["turn"]["options"]
        assert options and len(options) >= 2
        assert guesser_state["turn"]["audio_url"]
        assert guesser_state["turn"]["attempts_left"] == 2
        # Guesser never sees the image.
        assert guesser_state["turn"].get("card_image_url") in (None, "")

        # Attach quality before scoring so package enqueue is exercised.
        await seeded_store.set_turn_quality_for_tests(
            turn.id,
            {
                "is_speech": True,
                "single_speaker": True,
                "audio_quality_ok": True,
                "duplicate": False,
                "contamination_flag": False,
            },
        )

        correct_id = str(turn.card_id)
        # First round: wrong then correct (two-attempt path). Others: correct.
        if round_index == 0:
            wrong = next(opt for opt in options if opt["id"] != correct_id)
            wrong_resp = await game_client.post(
                "/api/turn/guess",
                headers=bearer(guesser_token),
                json={"option_id": wrong["id"]},
            )
            assert wrong_resp.status_code == 200
            mid = await _state(game_client, guesser_token)
            assert mid["phase"] == Phase.GUESSING.value
            assert mid["turn"]["attempts_left"] == 1

        ok = await game_client.post(
            "/api/turn/guess",
            headers=bearer(guesser_token),
            json={"option_id": correct_id},
        )
        assert ok.status_code == 200
        assert await seeded_store.count_jobs(kind="package", turn_id=turn.id) == 1
        # Idempotent package enqueue.
        await seeded_store.enqueue_job(kind="package", turn_id=turn.id)
        assert await seeded_store.count_jobs(kind="package", turn_id=turn.id) == 1

        # Roles swap for the next round.
        speaker_token, guesser_token = guesser_token, speaker_token

    final_a = await _state(game_client, token_a)
    final_b = await _state(game_client, token_b)
    assert final_a["player"]["rounds_played"] == 3
    assert final_b["player"]["rounds_played"] == 3
    # Validated-only scoring: 3 validations * 10 points each participant.
    assert final_a["player"]["score"] == 30
    assert final_b["player"]["score"] == 30

    board = await game_client.get("/api/leaderboard?top=5")
    assert board.status_code == 200
    nicknames = {entry["nickname"] for entry in board.json()["entries"]}
    assert {"Asha", "Bala"} <= nicknames

    metrics = await game_client.get("/api/metrics")
    assert metrics.status_code == 200
    body = metrics.json()
    assert body["validated_pairs"] == 3
    assert body["language_count"] >= 2

    logger.info("test_two_players_three_rounds_no_label_leak completed")


async def test_label_absent_before_audio_http_contract(
    game_client: AsyncClient,
) -> None:
    """Assert speaking_view_image JSON omits label before audio upload.

    Args:
        game_client: HTTP client with memory-backed routes.

    Returns:
        None.
    """
    logger.info("test_label_absent_before_audio_http_contract called")
    token_a = await _join(game_client, "Cara", "kannada", ["english"])
    token_b = await _join(game_client, "Dev", "odia", ["english"])
    await game_client.post("/api/pair/request", headers=bearer(token_a))
    await game_client.post("/api/pair/request", headers=bearer(token_b))

    for token in (token_a, token_b):
        state = await _state(game_client, token)
        if state["phase"] == Phase.SPEAKING_VIEW_IMAGE.value:
            assert state["turn"]["label"] is None
            assert "fish" not in str(state["turn"]).lower() or state["turn"]["label"] is None
            # Explicit key presence: label may be null but must not carry text.
            assert state["turn"].get("label") in (None, {})
            if state["turn"].get("label"):
                raise AssertionError("label leaked before audio")
    logger.info("test_label_absent_before_audio_http_contract completed")


async def test_unclear_after_two_wrong_guesses(
    game_client: AsyncClient,
    seeded_store: MemoryGameStore,
    game_service: GameService,
) -> None:
    """Two wrong guesses score the turn as unclear with zero points.

    Args:
        game_client: HTTP client.
        seeded_store: In-memory store.
        game_service: Service for player resolution.

    Returns:
        None.
    """
    logger.info("test_unclear_after_two_wrong_guesses called")
    token_a = await _join(game_client, "Esha", "bengali", ["hindi"])
    token_b = await _join(game_client, "Farid", "marathi", ["hindi"])
    await game_client.post("/api/pair/request", headers=bearer(token_a))
    await game_client.post("/api/pair/request", headers=bearer(token_b))

    state_a = await _state(game_client, token_a)
    speaker_token, guesser_token = token_a, token_b
    if state_a["turn"]["role"] != "speaker":
        speaker_token, guesser_token = token_b, token_a

    await game_client.post(
        "/api/turn/audio",
        headers=bearer(speaker_token),
        files={"file": ("clip.webm", b"abc123", "audio/webm")},
    )
    await game_client.post("/api/turn/confirm-label", headers=bearer(speaker_token))
    guesser_state = await _state(game_client, guesser_token)
    options = guesser_state["turn"]["options"]
    player = await game_service.resolve_player(speaker_token)
    pair = await seeded_store.get_active_pair(player.id)
    assert pair is not None
    turn = await seeded_store.get_latest_turn(pair.id)
    assert turn is not None
    wrong_ids = [opt["id"] for opt in options if UUID(opt["id"]) != turn.card_id]
    assert len(wrong_ids) >= 2

    await game_client.post(
        "/api/turn/guess",
        headers=bearer(guesser_token),
        json={"option_id": wrong_ids[0]},
    )
    await game_client.post(
        "/api/turn/guess",
        headers=bearer(guesser_token),
        json={"option_id": wrong_ids[1]},
    )
    scored = await seeded_store.get_turn(turn.id)
    assert scored is not None
    assert scored.status == "scored"
    assert scored.outcome == "unclear"
    # No package without quality; triage exists from audio.
    assert await seeded_store.count_jobs(kind="triage", turn_id=turn.id) == 1
    assert await seeded_store.count_jobs(kind="package", turn_id=turn.id) == 0

    final = await _state(game_client, token_a)
    assert final["player"]["score"] == 0
    logger.info("test_unclear_after_two_wrong_guesses completed")
