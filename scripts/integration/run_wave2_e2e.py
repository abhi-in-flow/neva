"""CLI runner for the Wave 2 isolated end-to-end acceptance gate.

Operators provision an isolated Postgres database and marked ``DATA_DIR``,
export the required environment variables, and invoke this module directly.
The runner never creates or drops databases and refuses live development targets.
"""

from __future__ import annotations

import json
import logging
import sys

from tests.integration.guards import GuardViolation
from tests.integration.runner import configure_logging, run_from_env

LOGGER = logging.getLogger(__name__)

USAGE = """
Wave 2 isolated end-to-end acceptance gate

Required environment:
  export WAVE2_E2E_DATABASE_URL='postgresql://dialect:dialect_dev_only@127.0.0.1:55432/dialect_factory_isolated'
  export WAVE2_E2E_DATA_DIR='/tmp/neva_wave2_e2e_data'
  mkdir -p "$WAVE2_E2E_DATA_DIR"
  echo 'wave-2 e2e isolated' > "$WAVE2_E2E_DATA_DIR/.neva-isolated"

  export APP_ENVIRONMENT=load-test
  export INSTANCE_MARKER=wave2-load-isolated
  export WORKER_FAKE_GEMINI=true
  unset GEMINI_API_KEY

Optional:
  export WAVE2_E2E_API_PORT=18080
  export WAVE2_E2E_REQUIRE_FRONTEND=true

Run:
  uv run python -m scripts.integration.run_wave2_e2e

Or via pytest:
  uv run pytest tests/integration/test_wave2_e2e.py -v
""".strip()


def main() -> int:
    """Execute the live gate and print a JSON report on stdout.

    Returns:
        Process exit code ``0`` on pass, ``1`` on guard refusal or runtime failure.
    """
    configure_logging()
    LOGGER.info("main called")
    try:
        report = run_from_env()
    except GuardViolation as error:
        LOGGER.error("guard refusal code=%s message=%s", error.code, error.message)
        print(json.dumps({"status": "blocked", "code": error.code, "message": error.message}, indent=2))
        print(USAGE, file=sys.stderr)
        return 1
    except RuntimeError as error:
        LOGGER.error("live gate failed error=%s", error)
        print(json.dumps({"status": "failed", "message": str(error)}, indent=2))
        print(USAGE, file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, default=str))
    LOGGER.info("main completed status=%s", report.get("status"))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
