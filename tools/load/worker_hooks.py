"""Requested delayed/failing fake-worker contract for isolated load runs.

The harness does not start or configure the gauntlet worker. It reports the
exact Compose environment requested for the separately managed worker and marks
worker attestation as required for interpreting degradation measurements.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from tools.load.config import REQUIRED_ENVIRONMENT, REQUIRED_MARKER_VALUE

LOGGER = logging.getLogger(__name__)

WORKER_ENVIRONMENT = REQUIRED_ENVIRONMENT
WORKER_INSTANCE_MARKER = REQUIRED_MARKER_VALUE
WORKER_FAKE_GEMINI_ENV = "WORKER_FAKE_GEMINI"
WORKER_DELAY_ENV = "WORKER_FAKE_GEMINI_DELAY_SECONDS"
WORKER_FAILURE_RATE_ENV = "WORKER_FAKE_GEMINI_FAILURE_RATE"


@dataclass(frozen=True)
class WorkerBehaviorPlan:
    """Describe how the isolated worker should behave during a load run."""

    delay_s: float
    fail_rate: float
    attestation_required: bool = True

    def to_metadata(self) -> dict[str, object]:
        """Return redacted metadata for run summaries.

        Returns:
            JSON-serializable worker-behavior description.
        """
        LOGGER.info(
            "WorkerBehaviorPlan.to_metadata called delay_s=%s fail_rate=%s",
            self.delay_s,
            self.fail_rate,
        )
        return {
            "requested_environment": WORKER_ENVIRONMENT,
            "requested_instance_marker": WORKER_INSTANCE_MARKER,
            "requested_fake_gemini": True,
            "requested_delay_s": self.delay_s,
            "requested_failure_rate": self.fail_rate,
            "attestation_required": self.attestation_required,
            "injected_by_harness": False,
            "compose_environment": {
                "APP_ENVIRONMENT": WORKER_ENVIRONMENT,
                "INSTANCE_MARKER": WORKER_INSTANCE_MARKER,
                WORKER_FAKE_GEMINI_ENV: "true",
                WORKER_DELAY_ENV: str(self.delay_s),
                WORKER_FAILURE_RATE_ENV: str(self.fail_rate),
            },
        }


def build_worker_plan(delay_s: float, fail_rate: float) -> WorkerBehaviorPlan:
    """Build a worker-behavior plan from load configuration knobs.

    Args:
        delay_s: Artificial per-job delay to inject in the fake worker.
        fail_rate: Fraction of jobs that should fail in the fake worker.

    Returns:
        Worker behavior plan for orchestrator handoff.
    """
    LOGGER.info(
        "build_worker_plan called delay_s=%s fail_rate=%s",
        delay_s,
        fail_rate,
    )
    return WorkerBehaviorPlan(delay_s=delay_s, fail_rate=fail_rate)


DATABASE_OBSERVABILITY_REQUIREMENTS = [
    "Postgres: grant guarded load DSN read access to pg_stat_activity and pg_locks",
    "Postgres: grant guarded load DSN SELECT access to isolated jobs table",
]
