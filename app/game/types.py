"""Shared domain types and label helpers for the game core.

Defines store-facing record shapes and pure helpers for resolving
``cards.label_common`` text in the pair's shared language. Also owns the
canonical ``/api/metrics`` aggregate mapping helpers so Postgres (and tests)
share one definition of pitch numbers without reading mutable counters.

No I/O lives here.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# Micro-USD → USD divisor for ``api_calls.estimated_cost_microusd``.
MICROUSD_PER_USD = 1_000_000

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
    """Canonical throughput metrics exposed on ``/api/metrics``.

    Exact field definitions (frozen contract shape):

    - ``validated_pairs``: count of ``turns`` with ``outcome = 'validated'``.
    - ``training_eligible_pairs``: count of ``records`` with
      ``training_eligible IS TRUE`` (never ``metrics_counters``).
    - ``languages`` / ``language_count``: distinct normalized declared
      ``players.native_lang`` values for speakers of validated turns only;
      never counts unplayed registrations or ``common_langs``. A language
      remains counted when it is genuinely the validated speaker's native
      language, even if that same tag can be used as a bridge language.
    - ``gauntlet_pass_rate``: ``training_eligible`` records ÷ packaged
      records whose turn ``outcome = 'validated'``; ``None`` when the
      denominator is zero.
    - ``deck_images_per_minute`` / ``deck_cost_per_image_usd``: numeric
      keys from ``generation_metrics`` on the latest activated ``live``
      deck; ``None`` when no live deck or key evidence is missing.
    - ``cost_per_validated_sample_usd``: latest live deck
      ``generation_metrics.total_cost_usd`` plus successful
      ``gauntlet_triage`` API-call costs, divided by ``validated_pairs``.
      It is ``None`` until every validated turn is packaged, every packaged
      validated record has exactly one successful priced triage call, and the
      live deck total cost exists (see
      ``pipeline_cost_instrumentation_is_complete``).
    """

    validated_pairs: int = 0
    training_eligible_pairs: int = 0
    language_count: int = 0
    languages: list[str] = field(default_factory=list)
    cost_per_validated_sample_usd: float | None = None
    gauntlet_pass_rate: float | None = None
    deck_images_per_minute: float | None = None
    deck_cost_per_image_usd: float | None = None


@dataclass(frozen=True)
class MetricsAggregateRow:
    """Raw SQL/fake aggregate inputs for ``metrics_snapshot_from_aggregates``.

    Attributes:
        validated_pairs: Validated turn count.
        training_eligible_pairs: Eligible record count.
        packaged_validated_records: Packaged records joined to validated turns.
        languages: Speaker native langs already filtered or raw for filtering.
        gauntlet_triage_cost_microusd_sum: Sum of non-null successful
            ``gauntlet_triage`` API-call costs (micro-USD).
        successful_gauntlet_triage_call_count: All successful
            ``gauntlet_triage`` API-call rows, priced or unpriced.
        unpriced_gauntlet_triage_call_count: Successful ``gauntlet_triage``
            rows whose estimated cost is null.
        generation_metrics: Latest live deck ``generation_metrics`` JSON or None.
    """

    validated_pairs: int
    training_eligible_pairs: int
    packaged_validated_records: int
    languages: list[str]
    gauntlet_triage_cost_microusd_sum: int
    successful_gauntlet_triage_call_count: int
    unpriced_gauntlet_triage_call_count: int
    generation_metrics: dict[str, Any] | None


def normalize_language_tag(raw: str) -> str:
    """Normalize a free-text language declaration for pitch aggregation.

    Args:
        raw: Declared language string from ``players.native_lang``.

    Returns:
        Lower-cased, trimmed tag. Empty input becomes ``""``.
    """
    logger.info("normalize_language_tag called raw_len=%s", len(raw))
    normalized = raw.strip().lower()
    logger.info("normalize_language_tag completed out_len=%s", len(normalized))
    return normalized


def normalize_distinct_languages(languages: Iterable[str]) -> list[str]:
    """Normalize native-language values and return sorted distinct tags.

    Args:
        languages: Declared ``players.native_lang`` values from speakers of
            validated turns. Query design, not tag filtering, excludes
            ``common_langs`` and unplayed registrations.

    Returns:
        Sorted, normalized, non-empty distinct native-language tags. No tag is
        excluded based on whether it could also serve as a bridge language.
    """
    logger.info("normalize_distinct_languages called")
    seen: set[str] = set()
    for item in languages:
        text = normalize_language_tag(str(item))
        if not text:
            continue
        seen.add(text)
    out = sorted(seen)
    logger.info("normalize_distinct_languages completed count=%s", len(out))
    return out


def pipeline_cost_instrumentation_is_complete(
    *,
    validated_pairs: int,
    packaged_validated_records: int,
    deck_total_cost_usd: float | None,
    successful_gauntlet_triage_call_count: int,
    unpriced_gauntlet_triage_call_count: int,
) -> bool:
    """Conservatively gate complete, non-overlapping pipeline cost evidence.

    Evidence is complete only when validated work exists, the validated
    backlog is fully packaged, latest-live-deck ``total_cost_usd`` is numeric,
    each packaged validated record has one successful ``gauntlet_triage`` API
    call, and none of those successful triage calls is unpriced. Deck API rows
    are intentionally outside this gate because deck cost comes from
    ``generation_metrics`` and must not be counted twice.

    Args:
        validated_pairs: Number of validated turns.
        packaged_validated_records: Validated turns with canonical records.
        deck_total_cost_usd: Latest live deck's recorded total generation cost.
        successful_gauntlet_triage_call_count: Successful triage API-call rows.
        unpriced_gauntlet_triage_call_count: Successful triage rows missing cost.

    Returns:
        ``True`` only when the entire validated pipeline is costed.
    """
    logger.info(
        "pipeline_cost_instrumentation_is_complete called validated=%s "
        "packaged_validated=%s deck_total_present=%s triage_success=%s "
        "triage_unpriced=%s",
        validated_pairs,
        packaged_validated_records,
        deck_total_cost_usd is not None,
        successful_gauntlet_triage_call_count,
        unpriced_gauntlet_triage_call_count,
    )
    complete = (
        validated_pairs > 0
        and packaged_validated_records == validated_pairs
        and deck_total_cost_usd is not None
        and successful_gauntlet_triage_call_count == packaged_validated_records
        and unpriced_gauntlet_triage_call_count == 0
    )
    logger.info(
        "pipeline_cost_instrumentation_is_complete completed complete=%s",
        complete,
    )
    return complete


def compute_gauntlet_pass_rate(
    *,
    training_eligible_pairs: int,
    packaged_validated_records: int,
) -> float | None:
    """Compute eligible ÷ packaged-validated, or ``None`` when undefined.

    Args:
        training_eligible_pairs: Records with ``training_eligible`` true.
        packaged_validated_records: Packaged records for validated turns.

    Returns:
        Pass rate in ``[0, 1+]``, or ``None`` when the denominator is zero.
    """
    logger.info(
        "compute_gauntlet_pass_rate called eligible=%s packaged_validated=%s",
        training_eligible_pairs,
        packaged_validated_records,
    )
    if packaged_validated_records <= 0:
        logger.info("compute_gauntlet_pass_rate completed rate=None")
        return None
    rate = training_eligible_pairs / packaged_validated_records
    logger.info("compute_gauntlet_pass_rate completed rate=%s", rate)
    return rate


def compute_pipeline_cost_per_validated_sample_usd(
    *,
    validated_pairs: int,
    packaged_validated_records: int,
    deck_total_cost_usd: float | None,
    gauntlet_triage_cost_microusd_sum: int,
    successful_gauntlet_triage_call_count: int,
    unpriced_gauntlet_triage_call_count: int,
) -> float | None:
    """Derive complete pipeline USD cost per validated sample.

    Formula when complete:
    ``(deck_total_cost_usd + triage_microusd / 1_000_000) / validated_pairs``.
    Returns ``None`` unless the backlog and both non-overlapping cost components
    pass ``pipeline_cost_instrumentation_is_complete``. Never invents cost.

    Args:
        validated_pairs: Validated turn count (denominator).
        packaged_validated_records: Packaged records for validated turns.
        deck_total_cost_usd: Latest live deck's total recorded generation cost.
        gauntlet_triage_cost_microusd_sum: Priced successful triage cost sum.
        successful_gauntlet_triage_call_count: Successful triage row count.
        unpriced_gauntlet_triage_call_count: Successful unpriced triage count.

    Returns:
        Complete pipeline cost in USD per validated sample, or ``None``.
    """
    logger.info(
        "compute_pipeline_cost_per_validated_sample_usd called validated=%s "
        "packaged_validated=%s deck_total_present=%s triage_cost_microusd=%s "
        "triage_success=%s triage_unpriced=%s",
        validated_pairs,
        packaged_validated_records,
        deck_total_cost_usd is not None,
        gauntlet_triage_cost_microusd_sum,
        successful_gauntlet_triage_call_count,
        unpriced_gauntlet_triage_call_count,
    )
    if not pipeline_cost_instrumentation_is_complete(
        validated_pairs=validated_pairs,
        packaged_validated_records=packaged_validated_records,
        deck_total_cost_usd=deck_total_cost_usd,
        successful_gauntlet_triage_call_count=successful_gauntlet_triage_call_count,
        unpriced_gauntlet_triage_call_count=unpriced_gauntlet_triage_call_count,
    ):
        logger.info(
            "compute_pipeline_cost_per_validated_sample_usd completed cost=None"
        )
        return None
    assert deck_total_cost_usd is not None
    gauntlet_cost_usd = gauntlet_triage_cost_microusd_sum / MICROUSD_PER_USD
    cost = (deck_total_cost_usd + gauntlet_cost_usd) / validated_pairs
    logger.info(
        "compute_pipeline_cost_per_validated_sample_usd completed cost=%s",
        cost,
    )
    return cost


def extract_deck_generation_metric(
    generation_metrics: dict[str, Any] | None,
    key: str,
) -> float | None:
    """Read one numeric deck metric key, or ``None`` without evidence.

    Args:
        generation_metrics: ``decks.generation_metrics`` mapping or ``None``.
        key: Expected key such as ``images_per_minute`` or
            ``cost_per_image_usd``.

    Returns:
        Finite float value, or ``None`` when missing/non-numeric.
    """
    logger.info(
        "extract_deck_generation_metric called key=%s has_metrics=%s",
        key,
        generation_metrics is not None,
    )
    if not isinstance(generation_metrics, dict):
        logger.info("extract_deck_generation_metric completed value=None")
        return None
    raw = generation_metrics.get(key)
    if isinstance(raw, bool) or raw is None:
        logger.info("extract_deck_generation_metric completed value=None")
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        if not math.isfinite(value):
            logger.info("extract_deck_generation_metric completed value=None")
            return None
        logger.info("extract_deck_generation_metric completed value=%s", value)
        return value
    logger.info(
        "extract_deck_generation_metric completed value=None raw_type=%s",
        type(raw).__name__,
    )
    return None


def metrics_snapshot_from_aggregates(row: MetricsAggregateRow) -> MetricsSnapshot:
    """Map canonical SQL/fake aggregates into a ``MetricsSnapshot``.

    Args:
        row: Aggregate counts and optional deck/API cost evidence.

    Returns:
        Snapshot matching frozen ``MetricsResponse`` field semantics.

    Side effects:
        Logs safe aggregate metadata only (counts and nullability flags).
    """
    logger.info(
        "metrics_snapshot_from_aggregates called validated_pairs=%s "
        "training_eligible_pairs=%s packaged_validated=%s lang_in=%s "
        "triage_success=%s triage_unpriced=%s has_deck_metrics=%s",
        row.validated_pairs,
        row.training_eligible_pairs,
        row.packaged_validated_records,
        len(row.languages),
        row.successful_gauntlet_triage_call_count,
        row.unpriced_gauntlet_triage_call_count,
        row.generation_metrics is not None,
    )
    languages = normalize_distinct_languages(row.languages)
    snapshot = MetricsSnapshot(
        validated_pairs=row.validated_pairs,
        training_eligible_pairs=row.training_eligible_pairs,
        language_count=len(languages),
        languages=languages,
        cost_per_validated_sample_usd=compute_pipeline_cost_per_validated_sample_usd(
            validated_pairs=row.validated_pairs,
            packaged_validated_records=row.packaged_validated_records,
            deck_total_cost_usd=extract_deck_generation_metric(
                row.generation_metrics,
                "total_cost_usd",
            ),
            gauntlet_triage_cost_microusd_sum=(
                row.gauntlet_triage_cost_microusd_sum
            ),
            successful_gauntlet_triage_call_count=(
                row.successful_gauntlet_triage_call_count
            ),
            unpriced_gauntlet_triage_call_count=(
                row.unpriced_gauntlet_triage_call_count
            ),
        ),
        gauntlet_pass_rate=compute_gauntlet_pass_rate(
            training_eligible_pairs=row.training_eligible_pairs,
            packaged_validated_records=row.packaged_validated_records,
        ),
        deck_images_per_minute=extract_deck_generation_metric(
            row.generation_metrics,
            "images_per_minute",
        ),
        deck_cost_per_image_usd=extract_deck_generation_metric(
            row.generation_metrics,
            "cost_per_image_usd",
        ),
    )
    logger.info(
        "metrics_snapshot_from_aggregates completed validated_pairs=%s "
        "training_eligible_pairs=%s language_count=%s gauntlet_pass_rate=%s "
        "cost_present=%s deck_ipm_present=%s deck_cost_present=%s",
        snapshot.validated_pairs,
        snapshot.training_eligible_pairs,
        snapshot.language_count,
        snapshot.gauntlet_pass_rate,
        snapshot.cost_per_validated_sample_usd is not None,
        snapshot.deck_images_per_minute is not None,
        snapshot.deck_cost_per_image_usd is not None,
    )
    return snapshot


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
