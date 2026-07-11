"""Scenario planners and executors for venue-scale load simulation.

Implements the 200-client polling storm, jittered/synchronized action bursts,
bounded uploads/actions, and seeding helpers. Scenarios never bypass safety
guards and honor action/upload caps.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from tools.load.client import ApiSession, LoadClient, make_burst_schedule, seed_clients
from tools.load.config import LoadConfig
from tools.load.guards import validate_mutating_allowed
from tools.load.samplers import SamplerBundle
from tools.load.worker_hooks import build_worker_plan

LOGGER = logging.getLogger(__name__)

ACTION_SAMPLE_CADENCE = 10


@dataclass
class ScenarioResult:
    """Outcome metadata for one executed scenario."""

    name: str
    clients_seeded: int
    polls_executed: int
    actions_executed: int
    uploads_executed: int
    bounded: bool


def plan_poll_rounds(config: LoadConfig) -> int:
    """Compute how many poll rounds fit in the configured duration.

    Args:
        config: Load configuration.

    Returns:
        Number of poll rounds per client.
    """
    LOGGER.info(
        "plan_poll_rounds called duration_s=%s poll_interval_s=%s",
        config.duration_s,
        config.poll_interval_s,
    )
    return max(1, int(config.duration_s // config.poll_interval_s))


def execute_poll_storm(
    session: ApiSession,
    *,
    sampler_bundle: SamplerBundle | None = None,
) -> ScenarioResult:
    """Run the canonical 200-client ``/api/state`` polling scenario.

    Args:
        session: API session with seeded or to-be-seeded clients.
        sampler_bundle: Optional observability samplers.

    Returns:
        Scenario result with bounded poll counts.

    Side effects:
        Performs HTTP polling unless transport is a recording stub.
    """
    LOGGER.info(
        "execute_poll_storm called client_count=%s seed_clients=%s",
        session.config.client_count,
        session.config.seed_clients,
    )
    config = session.config
    if not session.clients:
        seed_count = config.seed_clients or config.client_count
        validate_mutating_allowed(config)
        seeded = seed_clients(session, count=seed_count)
    else:
        seeded = session.clients
    clients = seeded[: config.client_count]
    rounds = plan_poll_rounds(config)
    polls = 0
    for _round in range(rounds):
        for client in clients:
            session.poll_state(client)
            polls += 1
        if sampler_bundle is not None:
            session.metrics.record_sampler(
                {"phase": "during", "poll_round": _round + 1, **sampler_bundle.sample_all()}
            )
        if _round + 1 < rounds:
            time.sleep(config.poll_interval_s)
    return ScenarioResult(
        name="poll_storm",
        clients_seeded=len(clients),
        polls_executed=polls,
        actions_executed=0,
        uploads_executed=0,
        bounded=True,
    )


def execute_action_burst(
    session: ApiSession,
    *,
    sampler_bundle: SamplerBundle | None = None,
    offsets: list[float] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ScenarioResult:
    """Run bounded join/pair/action traffic with jittered or sync offsets.

    Args:
        session: API session.
        sampler_bundle: Optional observability samplers.
        offsets: Optional deterministic per-client offsets relative to one
            burst start.
        sleep: Injectable sleeper used by each concurrent client task.
        clock: Injectable monotonic clock.

    Returns:
        Scenario result with action/upload counts.

    Side effects:
        May perform mutating HTTP calls when enabled in config.
    """
    LOGGER.info("execute_action_burst called enable_actions=%s", session.config.enable_actions)
    config = session.config
    validate_mutating_allowed(config)
    if not config.enable_actions and not config.enable_uploads:
        return ScenarioResult(
            name="action_burst",
            clients_seeded=0,
            polls_executed=0,
            actions_executed=0,
            uploads_executed=0,
            bounded=True,
        )
    burst_cap = min(config.client_count, config.action_burst_size)
    if session.clients:
        clients = session.clients[:burst_cap]
    else:
        requested_clients = config.seed_clients or burst_cap
        clients = seed_clients(session, count=min(requested_clients, burst_cap))
    schedule = offsets or make_burst_schedule(config, len(clients))
    if len(schedule) < len(clients):
        raise ValueError("offset schedule must include every burst client")
    actions = 0
    uploads = 0
    polls = 0
    burst_started = clock()
    workers = min(config.max_concurrent_request_workers, len(clients))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [
            pool.submit(
                _execute_burst_client,
                session,
                client,
                offset,
                burst_started=burst_started,
                sleep=sleep,
                clock=clock,
            )
            for client, offset in zip(clients, schedule, strict=True)
        ]
        for index, future in enumerate(as_completed(futures), start=1):
            client_actions, client_uploads, client_polls = future.result()
            actions += client_actions
            uploads += client_uploads
            polls += client_polls
            if sampler_bundle is not None and index % ACTION_SAMPLE_CADENCE == 0:
                session.metrics.record_sampler(
                    {
                        "phase": "during",
                        "action_client": index,
                        **sampler_bundle.sample_all(),
                    }
                )
    if sampler_bundle is not None and len(clients) % ACTION_SAMPLE_CADENCE:
        session.metrics.record_sampler(
            {"phase": "during", "action_client": len(clients), **sampler_bundle.sample_all()}
        )
    return ScenarioResult(
        name="action_burst",
        clients_seeded=len(clients),
        polls_executed=polls,
        actions_executed=actions,
        uploads_executed=uploads,
        bounded=True,
    )


def _wait_for_burst_offset(
    offset_s: float,
    *,
    burst_started: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> None:
    """Wait until an offset measured from one shared burst start.

    Args:
        offset_s: Client offset relative to ``burst_started``.
        burst_started: Shared monotonic burst timestamp.
        sleep: Injectable sleeper.
        clock: Injectable monotonic clock.

    Side effects:
        Sleeps only for the remaining relative delay.
    """
    LOGGER.info(
        "_wait_for_burst_offset called offset_s=%s burst_started=%s",
        offset_s,
        burst_started,
    )
    remaining = burst_started + max(0.0, offset_s) - clock()
    if remaining > 0:
        sleep(remaining)


def _execute_burst_client(
    session: ApiSession,
    client: LoadClient,
    offset_s: float,
    *,
    burst_started: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> tuple[int, int, int]:
    """Execute one client's bounded action sequence inside the burst executor.

    Args:
        session: Shared API session.
        client: Simulated authenticated client.
        offset_s: Start offset relative to the shared burst timestamp.
        burst_started: Shared monotonic burst timestamp.
        sleep: Injectable sleeper.
        clock: Injectable monotonic clock.

    Returns:
        Tuple of ``(actions, uploads, polls)`` completed.

    Side effects:
        Performs bounded pair, state, upload, and confirm HTTP requests.
    """
    LOGGER.info(
        "_execute_burst_client called client_id=%s offset_s=%s",
        client.client_id,
        offset_s,
    )
    _wait_for_burst_offset(
        offset_s,
        burst_started=burst_started,
        sleep=sleep,
        clock=clock,
    )
    actions = 0
    uploads = 0
    session.request_pair(client)
    actions += 1
    session.poll_state(client)
    if (
        session.config.enable_uploads
        and client.uploads_taken < session.config.max_uploads_per_client
    ):
        try:
            session.upload_fixture_audio(client)
            uploads += 1
        except RuntimeError:
            LOGGER.info(
                "_execute_burst_client upload skipped client_id=%s",
                client.client_id,
            )
    if (
        session.config.enable_actions
        and client.actions_taken < session.config.max_actions_per_client
    ):
        try:
            session.confirm_label(client)
            actions += 1
        except RuntimeError:
            LOGGER.info(
                "_execute_burst_client confirm skipped client_id=%s",
                client.client_id,
            )
    return actions, uploads, 1


def execute_mixed(
    session: ApiSession,
    *,
    sampler_bundle: SamplerBundle | None = None,
) -> ScenarioResult:
    """Run polling plus a bounded tail burst in one scenario.

    Args:
        session: API session.
        sampler_bundle: Optional observability samplers.

    Returns:
        Combined scenario result metadata.
    """
    LOGGER.info("execute_mixed called")
    poll_result = execute_parallel_poll_storm(session, sampler_bundle=sampler_bundle)
    burst_result = execute_action_burst(session, sampler_bundle=sampler_bundle)
    return ScenarioResult(
        name="mixed",
        clients_seeded=poll_result.clients_seeded,
        polls_executed=poll_result.polls_executed + burst_result.polls_executed,
        actions_executed=burst_result.actions_executed,
        uploads_executed=burst_result.uploads_executed,
        bounded=poll_result.bounded and burst_result.bounded,
    )


def execute_parallel_poll_storm(
    session: ApiSession,
    *,
    sampler_bundle: SamplerBundle | None = None,
) -> ScenarioResult:
    """Execute one poll round across clients using a thread pool.

    Args:
        session: API session.
        sampler_bundle: Optional observability samplers.

    Returns:
        Scenario result for parallel polling rounds.
    """
    LOGGER.info(
        "execute_parallel_poll_storm called client_count=%s max_workers=%s",
        session.config.client_count,
        session.config.max_concurrent_request_workers,
    )
    config = session.config
    if not session.clients:
        validate_mutating_allowed(config)
        seed_clients(session, count=config.seed_clients or config.client_count)
    clients = session.clients[: config.client_count]
    rounds = plan_poll_rounds(config)
    polls = 0
    workers = min(config.max_concurrent_request_workers, max(1, len(clients)))
    for _round in range(rounds):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(session.poll_state, client) for client in clients]
            for future in as_completed(futures):
                future.result()
                polls += 1
        if sampler_bundle is not None:
            session.metrics.record_sampler(
                {"phase": "during", "poll_round": _round + 1, **sampler_bundle.sample_all()}
            )
        if _round + 1 < rounds:
            time.sleep(config.poll_interval_s)
    return ScenarioResult(
        name="parallel_poll_storm",
        clients_seeded=len(clients),
        polls_executed=polls,
        actions_executed=0,
        uploads_executed=0,
        bounded=True,
    )


def select_scenario(name: str):
    """Return the scenario executor function for a scenario name.

    Args:
        name: Scenario identifier from configuration.

    Returns:
        Callable scenario executor.

    Raises:
        ValueError: When the scenario name is unknown.
    """
    LOGGER.info("select_scenario called name=%s", name)
    scenarios = {
        "poll_storm": execute_poll_storm,
        "parallel_poll_storm": execute_parallel_poll_storm,
        "action_burst": execute_action_burst,
        "mixed": execute_mixed,
    }
    if name not in scenarios:
        raise ValueError(f"unknown scenario: {name}")
    return scenarios[name]


def scenario_plan_metadata(config: LoadConfig) -> dict[str, object]:
    """Return non-mutating scenario planning metadata for dry-run output.

    Args:
        config: Loaded load configuration.

    Returns:
        JSON-serializable plan without performing I/O.
    """
    LOGGER.info("scenario_plan_metadata called scenario=%s", config.scenario)
    rounds = plan_poll_rounds(config)
    return {
        "scenario": config.scenario,
        "client_count": config.client_count,
        "max_concurrent_request_workers": config.max_concurrent_request_workers,
        "poll_rounds": rounds,
        "expected_poll_requests": config.client_count * rounds,
        "burst_mode": config.burst_mode,
        "action_burst_size": config.action_burst_size,
        "max_actions_per_client": config.max_actions_per_client,
        "max_uploads_per_client": config.max_uploads_per_client,
        "worker_plan": build_worker_plan(config.worker_delay_s, config.worker_fail_rate).to_metadata(),
    }


def assert_client_bounds(client: LoadClient, config: LoadConfig) -> bool:
    """Return whether a client remains within configured action/upload bounds.

    Args:
        client: Simulated client.
        config: Load configuration.

    Returns:
        ``True`` when both action and upload counts are within bounds.
    """
    LOGGER.info(
        "assert_client_bounds called client_id=%s actions=%s uploads=%s",
        client.client_id,
        client.actions_taken,
        client.uploads_taken,
    )
    return (
        client.actions_taken <= config.max_actions_per_client
        and client.uploads_taken <= config.max_uploads_per_client
    )
