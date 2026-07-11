"""Opt-in live gate test for the Wave 2 isolated end-to-end acceptance path.

The live test skips with an explicit reason unless both required environment
variables are set, guards pass, and Postgres is reachable. It never reports
pass when Postgres is unavailable.
"""

from __future__ import annotations

import logging

import pytest

from tests.integration.config import load_config_from_env
from tests.integration.database import postgres_reachable
from tests.integration.guards import GuardViolation, validate_config
from tests.integration.runner import run_wave2_e2e

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_wave2_isolated_end_to_end_live_gate() -> None:
    """Run the full isolated acceptance gate when explicitly configured.

    Returns:
        None.

    Side effects:
        Mutates only the operator-provisioned isolated database and DATA_DIR
        when all guards pass and Postgres is reachable.
    """
    LOGGER.info("test_wave2_isolated_end_to_end_live_gate called")
    config = load_config_from_env()
    if config is None:
        pytest.skip(
            "live gate not run: set WAVE2_E2E_DATABASE_URL and WAVE2_E2E_DATA_DIR "
            "to an operator-provisioned isolated target"
        )

    try:
        validate_config(config)
    except GuardViolation as error:
        pytest.fail(f"live gate guard refusal: {error.message} ({error.code})")

    if not await postgres_reachable(config.database_url):
        pytest.skip(
            f"live gate not run: Postgres unavailable for database={config.database_name}"
        )

    report = await run_wave2_e2e(config)
    assert report["status"] == "pass"
    assert report["post_conditions"]["jobs"]["triage"] == 1
    assert report["post_conditions"]["jobs"]["package"] == 1
    assert report["post_conditions"]["shard_line_count"] == 1
    if not config.require_frontend:
        assert report["frontend"]["required"] is False
        assert "cut" in report["frontend"]
    LOGGER.info("test_wave2_isolated_end_to_end_live_gate completed status=pass")
