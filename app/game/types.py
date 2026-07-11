"""Shared domain types and label helpers for the game core.

Defines store-facing record shapes and pure helpers for resolving
``cards.label_common`` text in the pair's shared language. No I/O lives here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class PlayerRecord:
    """Persisted player row used by game services.

    Attributes:
        id: Player UUID.
        nickname: Display name.
        native_lang: Declared native language tag.
        common_langs: Languages usable for guessing/labels.
        session_token_hash: SHA-256 hex of the bearer token.
        created_at: Insertion timestamp.
    """

    id: UUID
    nickname: str
    native_lang: str
    common_langs: list[str]
    session_token_hash: str
    created_at: datetime


@dataclass
class PairRecord:
    """Active or historical pairing between two players."""

    id: UUID
    player_a: UUID
    player_b: UUID
    common_lang: str
    status: str
    created_at: datetime


@dataclass
class CardRecord:
    """Verified picture card from a live deck."""

    id: UUID
    deck_id: UUID
    image_path: str
    label_common: dict[str, Any]
    decoys: list[str]
    verified: bool


@dataclass
class TurnRecord:
    """Single elicitation/validation turn for a pair."""

    id: UUID
    pair_id: UUID
    speaker_id: UUID
    guesser_id: UUID
    card_id: UUID
    status: str
    audio_path: str | None
    audio_flac_path: str | None
    duration_s: float | None
    quality: dict[str, Any] | None
    attempts: int
    outcome: str
    created_at: datetime


@dataclass
class JobRecord:
    """Async gauntlet job row (``triage`` or ``package``)."""

    id: UUID
    kind: str
    payload: dict[str, Any]
    status: str
    tries: int = 0
    last_error: str | None = None
    created_at: datetime | None = None


@dataclass
class PlayerStats:
    """Aggregated score and round counts for one player."""

    score: int = 0
    rounds_played: int = 0
    rank: int | None = None


@dataclass
class LeaderboardRow:
    """Nickname and score for venue displays."""

    nickname: str
    score: int


@dataclass
class MetricsSnapshot:
    """Throughput metrics exposed on ``/api/metrics``."""

    validated_pairs: int = 0
    training_eligible_pairs: int = 0
    language_count: int = 0
    languages: list[str] = field(default_factory=list)
    cost_per_validated_sample_usd: float | None = None
    gauntlet_pass_rate: float | None = None
    deck_images_per_minute: float | None = None
    deck_cost_per_image_usd: float | None = None


@dataclass
class StateBundle:
    """Raw facts gathered in one store round-trip for state composition."""

    player: PlayerRecord
    queued: bool
    pair: PairRecord | None
    partner: PlayerRecord | None
    turn: TurnRecord | None
    card: CardRecord | None
    previous_scored: TurnRecord | None
    stats: PlayerStats
    leaderboard_top: list[LeaderboardRow]
    decoy_labels: dict[str, str] = field(default_factory=dict)


def normalize_common_langs(raw: Any) -> list[str]:
    """Normalize JSON/list language payloads into a clean string list.

    Args:
        raw: JSONB-decoded value, JSON string, or sequence of languages.

    Returns:
        Deduplicated list of non-empty language strings preserving order.
    """
    logger.info("normalize_common_langs called raw_type=%s", type(raw).__name__)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = [raw]
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    logger.info("normalize_common_langs completed count=%s", len(out))
    return out


def resolve_label_text(label_common: dict[str, Any] | Any, common_lang: str) -> str:
    """Pick the display label for a card in the pair's common language.

    Args:
        label_common: Card ``label_common`` JSON object. Supports language-keyed
            maps (``{"hi": "..."}``), a ``text`` field, or a bare string.
        common_lang: Shared language tag for the active pair.

    Returns:
        Resolved label string. Falls back to ``en``, then ``text``, then the
        first string value.

    Side effects:
        Logs the resolution path without logging the full label when avoidable;
        label text length is logged instead for privacy-adjacent hygiene.
    """
    logger.info(
        "resolve_label_text called common_lang=%s label_type=%s",
        common_lang,
        type(label_common).__name__,
    )
    if isinstance(label_common, str):
        try:
            label_common = json.loads(label_common)
        except json.JSONDecodeError:
            logger.info("resolve_label_text completed via_raw_string len=%s", len(label_common))
            return label_common
    if not isinstance(label_common, dict):
        text = str(label_common)
        logger.info("resolve_label_text completed via_str len=%s", len(text))
        return text
    for key in (common_lang, "en", "text"):
        value = label_common.get(key)
        if isinstance(value, str) and value.strip():
            logger.info(
                "resolve_label_text completed via_key=%s len=%s",
                key,
                len(value),
            )
            return value.strip()
    for value in label_common.values():
        if isinstance(value, str) and value.strip():
            logger.info(
                "resolve_label_text completed via_first_value len=%s",
                len(value),
            )
            return value.strip()
    logger.info("resolve_label_text completed empty")
    return ""


def shared_languages(a: list[str], b: list[str]) -> list[str]:
    """Return languages present in both players' common-language lists.

    Args:
        a: First player's common languages.
        b: Second player's common languages.

    Returns:
        Intersection preserving ``a``'s order.
    """
    logger.info(
        "shared_languages called a_count=%s b_count=%s",
        len(a),
        len(b),
    )
    b_set = set(b)
    shared = [lang for lang in a if lang in b_set]
    logger.info("shared_languages completed shared_count=%s", len(shared))
    return shared
