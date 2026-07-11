"""Latency and throughput aggregation for load-test samples.

Collects per-request timings and HTTP status classes, then computes p50/p95/p99,
error rates, and requests-per-second for handoff to the orchestrator.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Iterable

LOGGER = logging.getLogger(__name__)


@dataclass
class RequestSample:
    """One completed HTTP attempt with timing and outcome metadata."""

    endpoint: str
    status_code: int | None
    latency_ms: float
    error: str | None = None
    client_id: int | None = None


@dataclass
class MetricsCollector:
    """Accumulate request samples and sampler snapshots for a run."""

    samples: list[RequestSample] = field(default_factory=list)
    sampler_snapshots: list[dict[str, object]] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float | None = None

    def record(self, sample: RequestSample) -> None:
        """Append one request sample.

        Args:
            sample: Completed request metadata.

        Side effects:
            Extends the in-memory sample list.
        """
        LOGGER.info(
            "MetricsCollector.record called endpoint=%s status_code=%s latency_ms=%.2f error=%s",
            sample.endpoint,
            sample.status_code,
            sample.latency_ms,
            sample.error,
        )
        self.samples.append(sample)

    def record_sampler(self, snapshot: dict[str, object]) -> None:
        """Append one observability sampler snapshot.

        Args:
            snapshot: Redacted sampler output.

        Side effects:
            Extends the sampler snapshot list.
        """
        LOGGER.info(
            "MetricsCollector.record_sampler called keys=%s",
            sorted(snapshot.keys()),
        )
        self.sampler_snapshots.append(snapshot)

    def finalize(self) -> None:
        """Mark the collector end time for throughput calculations.

        Side effects:
            Sets ``ended_at`` to the current monotonic clock.
        """
        LOGGER.info("MetricsCollector.finalize called sample_count=%s", len(self.samples))
        self.ended_at = time.monotonic()

    def percentile(self, values: list[float], pct: float) -> float:
        """Compute a percentile using nearest-rank on sorted values.

        Args:
            values: Numeric sample list.
            pct: Percentile in ``[0, 100]``.

        Returns:
            The percentile value, or ``0.0`` when ``values`` is empty.
        """
        LOGGER.info(
            "MetricsCollector.percentile called value_count=%s pct=%s",
            len(values),
            pct,
        )
        if not values:
            return 0.0
        ordered = sorted(values)
        rank = max(1, math.ceil((pct / 100.0) * len(ordered)))
        return ordered[rank - 1]

    def summarize(self) -> dict[str, object]:
        """Return aggregate latency, status, and throughput metrics.

        Returns:
            JSON-serializable summary including p50/p95/p99, status counts,
            error rate, and throughput.
        """
        LOGGER.info("MetricsCollector.summarize called sample_count=%s", len(self.samples))
        latencies = [sample.latency_ms for sample in self.samples]
        status_counts: dict[str, int] = {}
        error_count = 0
        for sample in self.samples:
            if sample.error or sample.status_code is None:
                error_count += 1
                key = "error"
            else:
                key = str(sample.status_code)
            status_counts[key] = status_counts.get(key, 0) + 1
        total = len(self.samples)
        duration_s = max(
            (self.ended_at or time.monotonic()) - self.started_at,
            0.001,
        )
        return {
            "request_count": total,
            "error_count": error_count,
            "error_rate": (error_count / total) if total else 0.0,
            "status_counts": status_counts,
            "latency_ms": {
                "p50": self.percentile(latencies, 50),
                "p95": self.percentile(latencies, 95),
                "p99": self.percentile(latencies, 99),
                "max": max(latencies) if latencies else 0.0,
            },
            "throughput_rps": total / duration_s,
            "duration_s": duration_s,
            "sampler_snapshots": list(self.sampler_snapshots),
        }


def merge_status_counts(samples: Iterable[RequestSample]) -> dict[str, int]:
    """Aggregate HTTP status counts from an iterable of samples.

    Args:
        samples: Request samples to fold.

    Returns:
        Mapping of status code string to occurrence count.
    """
    LOGGER.info("merge_status_counts called")
    counts: dict[str, int] = {}
    for sample in samples:
        key = "error" if sample.error or sample.status_code is None else str(sample.status_code)
        counts[key] = counts.get(key, 0) + 1
    return counts
