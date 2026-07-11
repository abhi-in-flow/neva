"""Metrics aggregation tests for load harness samples."""

from __future__ import annotations

import logging

from tools.load.metrics import MetricsCollector, RequestSample

LOGGER = logging.getLogger(__name__)


def test_percentile_aggregation_matches_nearest_rank() -> None:
    """Compute p50/p95/p99 using deterministic samples."""
    LOGGER.info("test_percentile_aggregation_matches_nearest_rank called")
    collector = MetricsCollector()
    for latency in [10.0, 20.0, 30.0, 40.0, 100.0]:
        collector.record(
            RequestSample(endpoint="state", status_code=200, latency_ms=latency),
        )
    collector.finalize()
    summary = collector.summarize()
    latency = summary["latency_ms"]
    assert latency["p50"] == 30.0
    assert latency["p95"] == 100.0
    assert latency["p99"] == 100.0
    assert latency["max"] == 100.0


def test_status_and_error_counts() -> None:
    """Aggregate HTTP status classes and transport errors."""
    LOGGER.info("test_status_and_error_counts called")
    collector = MetricsCollector()
    collector.record(RequestSample(endpoint="state", status_code=200, latency_ms=5.0))
    collector.record(RequestSample(endpoint="state", status_code=500, latency_ms=7.0))
    collector.record(
        RequestSample(endpoint="state", status_code=None, latency_ms=9.0, error="timeout"),
    )
    collector.finalize()
    summary = collector.summarize()
    assert summary["request_count"] == 3
    assert summary["error_count"] == 1
    assert summary["status_counts"]["200"] == 1
    assert summary["status_counts"]["500"] == 1
    assert summary["status_counts"]["error"] == 1
