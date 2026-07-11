"""HTTP scenario and durable post-conditions for the isolated Wave 2 gate."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from contracts.api_types import Phase
from tests.integration.audio import build_valid_wav_bytes
from tests.integration.config import Wave2E2EConfig
from tests.integration.database import (
    fetch_job_count,
    fetch_record_snapshot,
    fetch_turn_card_id,
    fetch_turn_quality,
)
from tests.integration.deck_seed import SEED_LABELS, label_in_state_payload
from tests.integration.guards import validate_remote_attestation
from tests.integration.process import run_worker_once

LOGGER = logging.getLogger(__name__)
GOLDEN_REQUIRED_KEYS = {
    "utterance_id",
    "audio_ref",
    "native_lang_tag",
    "common_lang_text",
    "image_id",
    "deck_id",
    "validation",
    "quality",
    "speaker_meta",
    "timestamps",
}


def bearer(token: str) -> dict[str, str]:
    """Build bearer authentication headers.

    Args:
        token: Raw session token.

    Returns:
        Authorization header mapping.
    """
    return {"Authorization": f"Bearer {token}"}


def _join(client: httpx.Client, *, nickname: str, native: str) -> str:
    """Join one E2E player.

    Args:
        client: API client.
        nickname: Display nickname.
        native: Native language.

    Returns:
        Session token.
    """
    response = client.post(
        "/api/join",
        json={
            "nickname": nickname,
            "native_lang": native,
            "common_langs": ["hindi", "english"],
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"join failed status={response.status_code}")
    return str(response.json()["session_token"])


def _state(client: httpx.Client, token: str) -> dict[str, Any]:
    """Fetch one player state.

    Args:
        client: API client.
        token: Session token.

    Returns:
        Parsed state object.
    """
    response = client.get("/api/state", headers=bearer(token))
    if response.status_code != 200:
        raise RuntimeError(f"state failed status={response.status_code}")
    return response.json()


async def _infer_turn_id_from_audio_path(config: Wave2E2EConfig) -> str:
    """Infer the active turn from its isolated audio filename.

    Args:
        config: Guarded E2E configuration.

    Returns:
        Turn UUID string.
    """
    candidates = sorted((config.data_dir / "audio").glob("*.webm"))
    if len(candidates) != 1:
        raise RuntimeError(f"expected one audio file count={len(candidates)}")
    return candidates[0].stem


async def execute_http_scenario(config: Wave2E2EConfig) -> dict[str, object]:
    """Drive two players through one validated and packaged round.

    Args:
        config: Guarded E2E configuration.

    Returns:
        Turn, leaderboard, and metrics evidence.
    """
    LOGGER.info("execute_http_scenario called api_base_url=%s", config.api_base_url)
    with httpx.Client(base_url=config.api_base_url, timeout=30.0) as client:
        validate_remote_attestation(client.get("/api/health").json(), config)
        token_a = _join(client, nickname="Asha", native="assamese")
        token_b = _join(client, nickname="Bala", native="tamil")
        first = client.post("/api/pair/request", headers=bearer(token_a))
        second = client.post("/api/pair/request", headers=bearer(token_b))
        if first.status_code != 200 or second.json().get("status") != "matched":
            raise RuntimeError("pair request did not match")

        state_a = _state(client, token_a)
        speaker_token, guesser_token = (
            (token_a, token_b)
            if state_a.get("turn", {}).get("role") == "speaker"
            else (token_b, token_a)
        )
        speaker_state = _state(client, speaker_token)
        if speaker_state.get("phase") != Phase.SPEAKING_VIEW_IMAGE.value:
            raise RuntimeError("speaker did not reach image phase")
        if speaker_state["turn"]["label"] is not None:
            raise RuntimeError("label leaked before upload")
        for label in SEED_LABELS:
            if label_in_state_payload(speaker_state, label):
                raise RuntimeError(f"seed label leaked label={label}")

        upload = client.post(
            "/api/turn/audio",
            headers=bearer(speaker_token),
            files={"file": ("clip.wav", build_valid_wav_bytes(), "audio/wav")},
        )
        if upload.status_code != 200 or upload.json().get("status") != "ok":
            raise RuntimeError("audio upload failed")
        post_audio = _state(client, speaker_token)
        label = post_audio.get("turn", {}).get("label")
        if not label:
            raise RuntimeError("label missing after upload")
        turn_id = await _infer_turn_id_from_audio_path(config)
        if await fetch_job_count(config.database_url, kind="triage", turn_id=turn_id) != 1:
            raise RuntimeError("triage job count mismatch")
        if run_worker_once(config) != 0:
            raise RuntimeError("triage worker failed")
        if await fetch_turn_quality(config.database_url, turn_id) is None:
            raise RuntimeError("triage quality missing")

        if client.post(
            "/api/turn/confirm-label",
            headers=bearer(speaker_token),
        ).status_code != 200:
            raise RuntimeError("label confirm failed")
        guesser_state = _state(client, guesser_token)
        if guesser_state.get("phase") != Phase.GUESSING.value:
            raise RuntimeError("guesser did not reach guessing")
        correct_id = await fetch_turn_card_id(config.database_url, turn_id)
        guess = client.post(
            "/api/turn/guess",
            headers=bearer(guesser_token),
            json={"option_id": correct_id},
        )
        if guess.status_code != 200:
            raise RuntimeError("guess failed")
        if await fetch_job_count(config.database_url, kind="package", turn_id=turn_id) != 1:
            raise RuntimeError("package job count mismatch")
        if run_worker_once(config) != 0:
            raise RuntimeError("package worker failed")
        leaderboard = client.get("/api/leaderboard?top=5").json()
        metrics = client.get("/api/metrics").json()
    return {
        "turn_id": turn_id,
        "label_text": label["text"],
        "correct_option_id": correct_id,
        "leaderboard": leaderboard,
        "metrics": metrics,
    }


async def assert_post_conditions(
    config: Wave2E2EConfig,
    *,
    turn_id: str,
) -> dict[str, object]:
    """Assert canonical record, shard, metrics, and job idempotency.

    Args:
        config: Guarded E2E configuration.
        turn_id: Completed turn UUID string.

    Returns:
        Structured post-condition evidence.
    """
    record = await fetch_record_snapshot(config.database_url, turn_id)
    if record is None or not record["training_eligible"]:
        raise RuntimeError("eligible canonical record missing")
    golden = record["golden"]
    if not isinstance(golden, dict):
        raise RuntimeError("golden record is not an object")
    missing = GOLDEN_REQUIRED_KEYS - set(golden)
    if missing:
        raise RuntimeError(f"golden keys missing={sorted(missing)}")
    if golden["utterance_id"] != turn_id or golden["validation"]["correct"] is not True:
        raise RuntimeError("golden identity or validation mismatch")

    shard_file = record.get("shard_file")
    shard_path = config.data_dir / "corpus" / str(shard_file)
    lines = [line for line in shard_path.read_text(encoding="utf-8").splitlines() if line]
    if len(lines) != 1 or json.loads(lines[0]).get("utterance_id") != turn_id:
        raise RuntimeError("shard output mismatch")
    triage = await fetch_job_count(config.database_url, kind="triage", turn_id=turn_id)
    package = await fetch_job_count(config.database_url, kind="package", turn_id=turn_id)
    if (triage, package) != (1, 1):
        raise RuntimeError("duplicate or missing jobs")
    with httpx.Client(base_url=config.api_base_url, timeout=10.0) as client:
        metrics = client.get("/api/metrics").json()
        leaderboard = client.get("/api/leaderboard?top=5").json()
    if metrics.get("validated_pairs") != 1:
        raise RuntimeError("validated metric mismatch")
    if metrics.get("training_eligible_pairs") != 1:
        raise RuntimeError("eligible metric mismatch")
    if metrics.get("language_count", 0) < 1:
        raise RuntimeError("language metric mismatch")
    nicknames = {entry["nickname"] for entry in leaderboard.get("entries", [])}
    if not {"Asha", "Bala"} <= nicknames:
        raise RuntimeError("leaderboard players missing")
    return {
        "record": {
            "turn_id": record["turn_id"],
            "training_eligible": record["training_eligible"],
            "shard_file": shard_file,
        },
        "golden_keys": sorted(golden),
        "shard_line_count": len(lines),
        "jobs": {"triage": triage, "package": package},
        "metrics": metrics,
        "leaderboard": leaderboard,
    }
