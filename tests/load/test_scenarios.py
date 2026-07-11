"""Scenario bound and planning tests."""

from __future__ import annotations

import logging
import threading
import time

import pytest

from tools.load.client import ApiSession, make_burst_schedule
from tools.load.config import REQUIRED_MARKER_VALUE, LoadConfig
from tools.load.guards import GuardViolation
from tools.load.metrics import MetricsCollector
from tools.load.scenarios import (
    assert_client_bounds,
    _wait_for_burst_offset,
    execute_action_burst,
    execute_mixed,
    execute_parallel_poll_storm,
    plan_poll_rounds,
    scenario_plan_metadata,
    select_scenario,
)
from tools.load.transport import RecordingTransport

LOGGER = logging.getLogger(__name__)


class BarrierTransport(RecordingTransport):
    """Recording transport that proves selected endpoint calls overlap."""

    def __init__(self, *, path: str, parties: int) -> None:
        """Initialize a barrier for one API path.

        Args:
            path: Endpoint suffix whose calls must overlap.
            parties: Number of concurrent calls expected.
        """
        LOGGER.info(
            "BarrierTransport.__init__ called path=%s parties=%s",
            path,
            parties,
        )
        super().__init__()
        self.path = path
        self.barrier = threading.Barrier(parties)
        self.arrival_times: list[float] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout_s: float = 10.0,
    ):
        """Block matching requests until all concurrent callers arrive.

        Args:
            method: HTTP verb.
            url: Absolute request URL.
            headers: Optional request headers.
            body: Optional request body.
            timeout_s: Request timeout.

        Returns:
            Canned recording response.
        """
        LOGGER.info(
            "BarrierTransport.request called method=%s url_length=%s",
            method,
            len(url),
        )
        if url.endswith(self.path):
            self.arrival_times.append(time.monotonic())
            self.barrier.wait(timeout=2.0)
        return super().request(
            method,
            url,
            headers=headers,
            body=body,
            timeout_s=timeout_s,
        )


def test_poll_round_planning_is_bounded(safe_config: LoadConfig) -> None:
    """Poll rounds derive from duration and interval."""
    LOGGER.info("test_poll_round_planning_is_bounded called")
    assert plan_poll_rounds(safe_config) == 15
    metadata = scenario_plan_metadata(safe_config)
    assert metadata["expected_poll_requests"] == 200 * 15
    assert metadata["max_concurrent_request_workers"] == 200


def test_worker_plan_matches_compose_contract(safe_config: LoadConfig) -> None:
    """Report requested fake-worker settings without claiming injection."""
    LOGGER.info("test_worker_plan_matches_compose_contract called")
    config = LoadConfig(
        **{
            **safe_config.__dict__,
            "worker_delay_s": 2.5,
            "worker_fail_rate": 0.25,
        }
    )
    worker = scenario_plan_metadata(config)["worker_plan"]
    assert worker["attestation_required"] is True
    assert worker["injected_by_harness"] is False
    assert worker["compose_environment"] == {
        "APP_ENVIRONMENT": "load-test",
        "INSTANCE_MARKER": "wave2-load-isolated",
        "WORKER_FAKE_GEMINI": "true",
        "WORKER_FAKE_GEMINI_DELAY_SECONDS": "2.5",
        "WORKER_FAKE_GEMINI_FAILURE_RATE": "0.25",
    }


def test_burst_schedule_respects_action_burst_size(safe_config: LoadConfig) -> None:
    """Jitter/sync schedules never exceed action_burst_size."""
    LOGGER.info("test_burst_schedule_respects_action_burst_size called")
    safe_config = LoadConfig(**{**safe_config.__dict__, "burst_mode": "sync", "action_burst_size": 5})
    offsets = make_burst_schedule(safe_config, client_count=200, rng=lambda: 0.25)
    assert len(offsets) == 5
    assert all(offset == 0.0 for offset in offsets)


def test_action_burst_is_bounded_with_recording_transport(safe_config: LoadConfig) -> None:
    """Mutating burst honors per-client action/upload caps."""
    LOGGER.info("test_action_burst_is_bounded_with_recording_transport called")
    config = LoadConfig(
        **{
            **safe_config.__dict__,
            "dry_run": False,
            "enable_actions": True,
            "enable_uploads": True,
            "seed_clients": 4,
            "action_burst_size": 4,
            "max_actions_per_client": 2,
            "max_uploads_per_client": 1,
            "scenario": "action_burst",
        }
    )
    session = ApiSession(
        config=config,
        transport=RecordingTransport(),
        metrics=MetricsCollector(),
    )
    result = execute_action_burst(session, offsets=[0.0] * 4)
    assert result.bounded is True
    for client in session.clients:
        assert assert_client_bounds(client, config)
        assert client.actions_taken <= config.max_actions_per_client
        assert client.uploads_taken <= config.max_uploads_per_client


def test_sync_action_burst_executes_concurrently(safe_config: LoadConfig) -> None:
    """Require synchronized pair requests to overlap in the bounded executor."""
    LOGGER.info("test_sync_action_burst_executes_concurrently called")
    config = LoadConfig(
        **{
            **safe_config.__dict__,
            "dry_run": False,
            "client_count": 4,
            "max_concurrent_request_workers": 4,
            "enable_actions": True,
            "seed_clients": 4,
            "action_burst_size": 4,
            "burst_mode": "sync",
        }
    )
    transport = BarrierTransport(path="/api/pair/request", parties=4)
    session = ApiSession(config=config, transport=transport, metrics=MetricsCollector())
    result = execute_action_burst(session, offsets=[0.0] * 4)

    assert result.actions_executed == 8
    assert len(transport.arrival_times) == 4
    assert max(transport.arrival_times) - min(transport.arrival_times) < 1.0


def test_jitter_offset_is_relative_to_shared_start() -> None:
    """Sleep only the remaining offset from one shared burst timestamp."""
    LOGGER.info("test_jitter_offset_is_relative_to_shared_start called")
    sleeps: list[float] = []
    _wait_for_burst_offset(
        0.5,
        burst_started=10.0,
        sleep=sleeps.append,
        clock=lambda: 10.2,
    )
    assert sleeps == pytest.approx([0.3])


def test_parallel_polling_uses_configured_client_concurrency(
    safe_config: LoadConfig,
) -> None:
    """Require one state-poll round to overlap across all configured clients."""
    LOGGER.info("test_parallel_polling_uses_configured_client_concurrency called")
    config = LoadConfig(
        **{
            **safe_config.__dict__,
            "dry_run": False,
            "client_count": 4,
            "max_concurrent_request_workers": 4,
            "seed_clients": 4,
            "duration_s": 0.1,
        }
    )
    transport = BarrierTransport(path="/api/state", parties=4)
    session = ApiSession(config=config, transport=transport, metrics=MetricsCollector())
    result = execute_parallel_poll_storm(session)

    assert result.polls_executed == 4
    assert len(transport.arrival_times) == 4


def test_mixed_scenario_reuses_seeded_clients_for_bounded_burst(
    safe_config: LoadConfig,
) -> None:
    """Reuse poll clients and cap action traffic without duplicate joins."""
    LOGGER.info("test_mixed_scenario_reuses_seeded_clients_for_bounded_burst called")
    config = LoadConfig(
        **{
            **safe_config.__dict__,
            "dry_run": False,
            "client_count": 4,
            "max_concurrent_request_workers": 4,
            "seed_clients": 4,
            "duration_s": 0.1,
            "enable_actions": True,
            "action_burst_size": 2,
            "burst_mode": "sync",
        }
    )
    session = ApiSession(
        config=config,
        transport=RecordingTransport(),
        metrics=MetricsCollector(),
    )
    result = execute_mixed(session)

    assert result.clients_seeded == 4
    assert result.actions_executed == 4
    assert len(session.clients) == 4


def test_seed_clients_requires_marker(safe_config: LoadConfig) -> None:
    """Seeding refuses to run without the isolated marker."""
    LOGGER.info("test_seed_clients_requires_marker called")
    config = LoadConfig(
        **{
            **safe_config.__dict__,
            "isolated_marker": "not-valid",
            "seed_clients": 1,
        }
    )
    session = ApiSession(
        config=config,
        transport=RecordingTransport(),
        metrics=MetricsCollector(),
    )
    with pytest.raises(GuardViolation):
        execute_action_burst(session)


def test_unknown_scenario_raises() -> None:
    """Reject unsupported scenario names."""
    LOGGER.info("test_unknown_scenario_raises called")
    with pytest.raises(ValueError):
        select_scenario("unknown")


def test_marker_constant_documented() -> None:
    """Keep the required marker stable for orchestrator docs."""
    LOGGER.info("test_marker_constant_documented called")
    assert REQUIRED_MARKER_VALUE == "wave2-load-isolated"
