"""Isolated unit tests for canonical ``/api/metrics`` definitions.

These tests exercise pure aggregate mapping and a fake asyncpg pool that
returns fixed SQL-shaped rows. They never touch Postgres, Gemini, or runtime
``data/``. Exact field definitions live in ``app.game.types`` docstrings.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.game.pg_store import PostgresGameStore
from app.game.types import (
    MICROUSD_PER_USD,
    MetricsAggregateRow,
    compute_gauntlet_pass_rate,
    compute_pipeline_cost_per_validated_sample_usd,
    extract_deck_generation_metric,
    metrics_snapshot_from_aggregates,
    normalize_distinct_languages,
    normalize_language_tag,
    pipeline_cost_instrumentation_is_complete,
)

logger = logging.getLogger(__name__)


def test_normalize_language_tag() -> None:
    """Native-language tags are trimmed and case-folded without exclusion."""
    logger.info("test_normalize_language_tag called")
    assert normalize_language_tag("  Assamese ") == "assamese"
    assert normalize_language_tag(" English ") == "english"
    assert normalize_language_tag("HI") == "hi"
    logger.info("test_normalize_language_tag completed")


def test_normalize_distinct_languages_keeps_native_bridge_capable_tags() -> None:
    """Native Assamese/Hindi/English remain after normalize and deduplicate."""
    logger.info(
        "test_normalize_distinct_languages_keeps_native_bridge_capable_tags called"
    )
    languages = normalize_distinct_languages(
        [
            "Assamese",
            "assamese",
            "English",
            "  ",
            "Hindi",
            "Bodo",
        ]
    )
    assert languages == ["assamese", "bodo", "english", "hindi"]
    logger.info(
        "test_normalize_distinct_languages_keeps_native_bridge_capable_tags completed"
    )


def test_gauntlet_pass_rate_null_when_denominator_zero() -> None:
    """Pass rate is null with zero packaged validated records."""
    logger.info("test_gauntlet_pass_rate_null_when_denominator_zero called")
    assert (
        compute_gauntlet_pass_rate(
            training_eligible_pairs=0,
            packaged_validated_records=0,
        )
        is None
    )
    assert (
        compute_gauntlet_pass_rate(
            training_eligible_pairs=3,
            packaged_validated_records=0,
        )
        is None
    )
    assert (
        compute_gauntlet_pass_rate(
            training_eligible_pairs=2,
            packaged_validated_records=4,
        )
        == 0.5
    )
    logger.info("test_gauntlet_pass_rate_null_when_denominator_zero completed")


def test_pipeline_cost_null_without_validated_pairs() -> None:
    """Pipeline cost remains null when there is no validated denominator."""
    logger.info("test_pipeline_cost_null_without_validated_pairs called")
    assert not pipeline_cost_instrumentation_is_complete(
        validated_pairs=0,
        packaged_validated_records=0,
        deck_total_cost_usd=1.0,
        successful_gauntlet_triage_call_count=0,
        unpriced_gauntlet_triage_call_count=0,
    )
    logger.info("test_pipeline_cost_null_without_validated_pairs completed")


def test_pipeline_cost_null_for_backlog_mismatch() -> None:
    """Pipeline cost remains null until all validated turns are packaged."""
    logger.info("test_pipeline_cost_null_for_backlog_mismatch called")
    actual = compute_pipeline_cost_per_validated_sample_usd(
        validated_pairs=5,
        packaged_validated_records=4,
        deck_total_cost_usd=1.0,
        gauntlet_triage_cost_microusd_sum=400_000,
        successful_gauntlet_triage_call_count=4,
        unpriced_gauntlet_triage_call_count=0,
    )
    assert actual is None
    logger.info("test_pipeline_cost_null_for_backlog_mismatch completed")


def test_pipeline_cost_null_without_deck_total() -> None:
    """Pipeline cost remains null without latest-live-deck total evidence."""
    logger.info("test_pipeline_cost_null_without_deck_total called")
    actual = compute_pipeline_cost_per_validated_sample_usd(
        validated_pairs=5,
        packaged_validated_records=5,
        deck_total_cost_usd=None,
        gauntlet_triage_cost_microusd_sum=500_000,
        successful_gauntlet_triage_call_count=5,
        unpriced_gauntlet_triage_call_count=0,
    )
    assert actual is None
    logger.info("test_pipeline_cost_null_without_deck_total completed")


def test_pipeline_cost_null_for_triage_call_count_mismatch() -> None:
    """Pipeline cost remains null unless each packaged record has one triage."""
    logger.info("test_pipeline_cost_null_for_triage_call_count_mismatch called")
    actual = compute_pipeline_cost_per_validated_sample_usd(
        validated_pairs=5,
        packaged_validated_records=5,
        deck_total_cost_usd=1.0,
        gauntlet_triage_cost_microusd_sum=400_000,
        successful_gauntlet_triage_call_count=4,
        unpriced_gauntlet_triage_call_count=0,
    )
    assert actual is None
    logger.info("test_pipeline_cost_null_for_triage_call_count_mismatch completed")


def test_pipeline_cost_null_for_unpriced_triage() -> None:
    """Pipeline cost remains null when any successful triage lacks cost."""
    logger.info("test_pipeline_cost_null_for_unpriced_triage called")
    actual = compute_pipeline_cost_per_validated_sample_usd(
        validated_pairs=5,
        packaged_validated_records=5,
        deck_total_cost_usd=1.0,
        gauntlet_triage_cost_microusd_sum=400_000,
        successful_gauntlet_triage_call_count=5,
        unpriced_gauntlet_triage_call_count=1,
    )
    assert actual is None
    logger.info("test_pipeline_cost_null_for_unpriced_triage completed")


def test_pipeline_cost_complete_formula() -> None:
    """Complete cost adds deck USD and triage micro-USD exactly once."""
    logger.info("test_pipeline_cost_complete_formula called")
    actual = compute_pipeline_cost_per_validated_sample_usd(
        validated_pairs=4,
        packaged_validated_records=4,
        deck_total_cost_usd=1.2,
        gauntlet_triage_cost_microusd_sum=800_000,
        successful_gauntlet_triage_call_count=4,
        unpriced_gauntlet_triage_call_count=0,
    )
    assert actual == (1.2 + 800_000 / MICROUSD_PER_USD) / 4
    logger.info("test_pipeline_cost_complete_formula completed")


def test_extract_deck_generation_metric_requires_evidence() -> None:
    """Deck metrics stay null without a numeric key on generation_metrics."""
    logger.info("test_extract_deck_generation_metric_requires_evidence called")
    assert extract_deck_generation_metric(None, "images_per_minute") is None
    assert extract_deck_generation_metric({}, "images_per_minute") is None
    assert extract_deck_generation_metric({"images_per_minute": "fast"}, "images_per_minute") is None
    assert extract_deck_generation_metric({"images_per_minute": True}, "images_per_minute") is None
    assert extract_deck_generation_metric({"images_per_minute": 12.5}, "images_per_minute") == 12.5
    assert extract_deck_generation_metric({"cost_per_image_usd": 0.04}, "cost_per_image_usd") == 0.04
    logger.info("test_extract_deck_generation_metric_requires_evidence completed")


def test_metrics_snapshot_from_aggregates_full_mapping() -> None:
    """Aggregate row maps to every frozen MetricsResponse-aligned field."""
    logger.info("test_metrics_snapshot_from_aggregates_full_mapping called")
    snapshot = metrics_snapshot_from_aggregates(
        MetricsAggregateRow(
            validated_pairs=10,
            training_eligible_pairs=7,
            packaged_validated_records=10,
            languages=["Bodo", "English", "Mising", "bodo", "Assamese"],
            gauntlet_triage_cost_microusd_sum=500_000,
            successful_gauntlet_triage_call_count=10,
            unpriced_gauntlet_triage_call_count=0,
            generation_metrics={
                "images_per_minute": 8.25,
                "cost_per_image_usd": 0.0336,
                "total_cost_usd": 1.5,
            },
        )
    )
    assert snapshot.validated_pairs == 10
    assert snapshot.training_eligible_pairs == 7
    assert snapshot.languages == ["assamese", "bodo", "english", "mising"]
    assert snapshot.language_count == 4
    assert snapshot.gauntlet_pass_rate == 0.7
    expected_cost = (1.5 + 500_000 / MICROUSD_PER_USD) / 10
    assert snapshot.cost_per_validated_sample_usd == expected_cost
    assert snapshot.deck_images_per_minute == 8.25
    assert snapshot.deck_cost_per_image_usd == 0.0336
    logger.info("test_metrics_snapshot_from_aggregates_full_mapping completed")


def test_metrics_snapshot_nulls_without_deck_or_cost_evidence() -> None:
    """Missing deck metrics and incomplete costs yield null pitch economics."""
    logger.info("test_metrics_snapshot_nulls_without_deck_or_cost_evidence called")
    snapshot = metrics_snapshot_from_aggregates(
        MetricsAggregateRow(
            validated_pairs=3,
            training_eligible_pairs=0,
            packaged_validated_records=0,
            languages=["english"],
            gauntlet_triage_cost_microusd_sum=0,
            successful_gauntlet_triage_call_count=0,
            unpriced_gauntlet_triage_call_count=0,
            generation_metrics=None,
        )
    )
    assert snapshot.language_count == 1
    assert snapshot.languages == ["english"]
    assert snapshot.gauntlet_pass_rate is None
    assert snapshot.cost_per_validated_sample_usd is None
    assert snapshot.deck_images_per_minute is None
    assert snapshot.deck_cost_per_image_usd is None
    logger.info("test_metrics_snapshot_nulls_without_deck_or_cost_evidence completed")


def _fake_metrics_row(**overrides: Any) -> dict[str, Any]:
    """Build one SQL-shaped metrics row for the Postgres store fake.

    Args:
        overrides: Field overrides for boundary cases.

    Returns:
        Mapping matching ``PostgresGameStore.metrics`` column aliases.
    """
    logger.info("_fake_metrics_row called override_keys=%s", sorted(overrides))
    base: dict[str, Any] = {
        "validated_pairs": 6,
        "training_eligible_pairs": 4,
        "packaged_validated_records": 6,
        "languages": ["assamese", "karbi", "mising"],
        "gauntlet_triage_cost_microusd_sum": 1_200_000,
        "successful_gauntlet_triage_call_count": 6,
        "unpriced_gauntlet_triage_call_count": 0,
        "generation_metrics": {
            "images_per_minute": 10.0,
            "cost_per_image_usd": 0.05,
            "total_cost_usd": 1.8,
        },
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_postgres_metrics_maps_exact_sql_result() -> None:
    """PostgresGameStore.metrics maps a fake fetchrow into canonical fields."""
    logger.info("test_postgres_metrics_maps_exact_sql_result called")
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=_fake_metrics_row())
    store = PostgresGameStore(pool)
    snapshot = await store.metrics()
    assert snapshot.validated_pairs == 6
    assert snapshot.training_eligible_pairs == 4
    assert snapshot.gauntlet_pass_rate == 4 / 6
    assert snapshot.languages == ["assamese", "karbi", "mising"]
    assert snapshot.language_count == 3
    expected_cost = (1.8 + 1_200_000 / MICROUSD_PER_USD) / 6
    assert snapshot.cost_per_validated_sample_usd == expected_cost
    assert snapshot.deck_images_per_minute == 10.0
    assert snapshot.deck_cost_per_image_usd == 0.05
    pool.fetchrow.assert_awaited_once()
    args, _kwargs = pool.fetchrow.await_args
    assert len(args) == 1
    sql = args[0]
    assert "FROM turns t" in sql
    assert "INNER JOIN players p ON p.id = t.speaker_id" in sql
    assert "t.outcome = 'validated'" in sql
    assert "p.native_lang" in sql
    assert "common_langs" not in sql
    assert "<> ALL" not in sql
    assert sql.count("FROM api_calls") == 3
    assert sql.count("operation = 'gauntlet_triage'") == 3
    logger.info("test_postgres_metrics_maps_exact_sql_result completed")


@pytest.mark.asyncio
async def test_postgres_metrics_incomplete_cost_and_partial_deck() -> None:
    """Store mapping nulls cost on unpriced successes and partial deck keys."""
    logger.info("test_postgres_metrics_incomplete_cost_and_partial_deck called")
    pool = MagicMock()
    pool.fetchrow = AsyncMock(
        return_value=_fake_metrics_row(
            successful_gauntlet_triage_call_count=2,
            unpriced_gauntlet_triage_call_count=1,
            gauntlet_triage_cost_microusd_sum=33600,
            generation_metrics={"images_per_minute": 9.0},
            languages=["Bodo", "English", "Assamese"],
            packaged_validated_records=0,
            training_eligible_pairs=0,
        )
    )
    store = PostgresGameStore(pool)
    snapshot = await store.metrics()
    assert snapshot.cost_per_validated_sample_usd is None
    assert snapshot.gauntlet_pass_rate is None
    assert snapshot.deck_images_per_minute == 9.0
    assert snapshot.deck_cost_per_image_usd is None
    assert snapshot.languages == ["assamese", "bodo", "english"]
    logger.info("test_postgres_metrics_incomplete_cost_and_partial_deck completed")


@pytest.mark.asyncio
async def test_postgres_metrics_json_string_payloads() -> None:
    """JSON string columns from drivers decode before mapping."""
    logger.info("test_postgres_metrics_json_string_payloads called")
    pool = MagicMock()
    pool.fetchrow = AsyncMock(
        return_value=_fake_metrics_row(
            languages='["nyishi"]',
            generation_metrics=(
                '{"images_per_minute": 3.5, "cost_per_image_usd": 0.02, '
                '"total_cost_usd": 0.3}'
            ),
            validated_pairs=2,
            packaged_validated_records=2,
            gauntlet_triage_cost_microusd_sum=100_000,
            successful_gauntlet_triage_call_count=2,
            unpriced_gauntlet_triage_call_count=0,
        )
    )
    store = PostgresGameStore(pool)
    snapshot = await store.metrics()
    assert snapshot.languages == ["nyishi"]
    assert snapshot.deck_images_per_minute == 3.5
    assert snapshot.deck_cost_per_image_usd == 0.02
    assert snapshot.cost_per_validated_sample_usd == (
        0.3 + 100_000 / MICROUSD_PER_USD
    ) / 2
    logger.info("test_postgres_metrics_json_string_payloads completed")
