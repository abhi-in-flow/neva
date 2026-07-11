"""Orchestration entrypoint for the Wave 2 isolated end-to-end acceptance gate.

Coordinates database bootstrap, API and worker subprocesses, deck seeding, the
HTTP scenario, optional frontend verification, and post-condition assertions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tests.integration.config import Wave2E2EConfig, config_log_meta, load_config_from_env
from tests.integration.database import prepare_database_target
from tests.integration.deck_seed import seed_live_deck
from tests.integration.frontend import verify_frontend
from tests.integration.guards import validate_config
from tests.integration.process import (
    ensure_runtime_directories,
    start_api_process,
    stop_process,
    wait_for_health,
)
from tests.integration.scenario import assert_post_conditions, execute_http_scenario

LOGGER = logging.getLogger(__name__)


def configure_logging() -> None:
    """Configure INFO logging for integration orchestration.

    Side effects:
        Attaches a stderr handler to the integration logger namespace.
    """
    LOGGER.info("configure_logging called")
    log = logging.getLogger("tests.integration")
    log.setLevel(logging.INFO)
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        log.addHandler(handler)


async def run_wave2_e2e(config: Wave2E2EConfig) -> dict[str, Any]:
    """Execute the full isolated end-to-end acceptance gate.

    Args:
        config: Guarded end-to-end configuration.

    Returns:
        JSON-serializable report with boundary results and optional frontend cut.

    Raises:
        GuardViolation: When safety guards fail before any mutation.
        RuntimeError: When bootstrap, API, worker, or assertions fail.
    """
    LOGGER.info("run_wave2_e2e called config=%s", config_log_meta(config))
    validate_config(config)
    ensure_runtime_directories(config)

    database_summary = await prepare_database_target(config)
    api_process = start_api_process(config)
    report: dict[str, Any] = {
        "status": "running",
        "config": config_log_meta(config),
        "database": database_summary,
    }
    try:
        health = wait_for_health(config, process=api_process)
        report["health"] = health
        report["deck"] = await seed_live_deck(config)
        scenario_summary = await execute_http_scenario(config)
        report["scenario"] = scenario_summary
        report["post_conditions"] = await assert_post_conditions(
            config,
            turn_id=str(scenario_summary["turn_id"]),
        )
        report["frontend"] = verify_frontend(config)
        report["status"] = "pass"
        LOGGER.info("run_wave2_e2e completed status=pass turn_id=%s", scenario_summary["turn_id"])
        return report
    except Exception:
        report["status"] = "failed"
        raise
    finally:
        stop_process(api_process)


def run_from_env() -> dict[str, Any]:
    """Load configuration from the environment and execute the live gate.

    Returns:
        JSON-serializable run report.

    Raises:
        RuntimeError: When required environment variables are missing.
        GuardViolation: When safety guards fail.
    """
    LOGGER.info("run_from_env called")
    config = load_config_from_env()
    if config is None:
        raise RuntimeError(
            "WAVE2_E2E_DATABASE_URL and WAVE2_E2E_DATA_DIR are required for the live gate"
        )
    return asyncio.run(run_wave2_e2e(config))
