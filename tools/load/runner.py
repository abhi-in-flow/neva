"""Load harness orchestration entrypoints."""

from __future__ import annotations

import json
import logging
from typing import Callable

from tools.load.client import ApiSession
from tools.load.config import LoadConfig, config_log_meta, load_config
from tools.load.guards import (
    GuardViolation,
    validate_config,
    validate_live_database_snapshot,
    validate_live_prerequisites,
    validate_remote_attestation,
)
from tools.load.metrics import MetricsCollector
from tools.load.samplers import (
    SamplerBundle,
    default_sampler_bundle,
    measure_recovery_to_baseline,
)
from tools.load.scenarios import scenario_plan_metadata, select_scenario
from tools.load.transport import HttpTransport, RecordingTransport, build_transport
from tools.load.worker_hooks import (
    DATABASE_OBSERVABILITY_REQUIREMENTS,
    build_worker_plan,
)

LOGGER = logging.getLogger(__name__)


def configure_logging() -> None:
    """Configure INFO logging for the load harness.

    Side effects:
        Attaches a stderr handler when the package logger has none.
    """
    LOGGER.info("configure_logging called")
    log = logging.getLogger("tools.load")
    log.setLevel(logging.INFO)
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        log.addHandler(handler)


def run_config_check(config: LoadConfig) -> dict[str, object]:
    """Validate configuration and return a redacted report without I/O.

    Args:
        config: Loaded configuration.

    Returns:
        JSON-serializable config-check report.

    Raises:
        GuardViolation: When safety checks fail.
    """
    LOGGER.info("run_config_check called")
    validate_config(config, require_marker=True)
    return {
        "status": "ok",
        "mode": "config-check",
        "config": config_log_meta(config),
        "plan": scenario_plan_metadata(config),
        "database_observability_requirements": DATABASE_OBSERVABILITY_REQUIREMENTS,
    }


def run_dry_run(config: LoadConfig, transport: HttpTransport | None = None) -> dict[str, object]:
    """Validate configuration and emit a zero-network execution plan.

    Args:
        config: Loaded configuration.
        transport: Optional transport; defaults to ``RecordingTransport``.

    Returns:
        Dry-run report. No database or filesystem access is performed.

    Raises:
        GuardViolation: When safety checks fail.
    """
    LOGGER.info("run_dry_run called")
    validate_config(config, require_marker=True)
    recording = transport or RecordingTransport()
    plan = scenario_plan_metadata(config)
    return {
        "status": "ok",
        "mode": "dry-run",
        "config": config_log_meta(config),
        "plan": plan,
        "http_calls": len(getattr(recording, "calls", [])),
        "database_observability_requirements": DATABASE_OBSERVABILITY_REQUIREMENTS,
    }


def run_live(
    config: LoadConfig,
    *,
    transport: HttpTransport | None = None,
    sampler_bundle: SamplerBundle | None = None,
) -> dict[str, object]:
    """Execute the configured scenario against the isolated target.

    Args:
        config: Loaded configuration.
        transport: Optional HTTP transport override.
        sampler_bundle: Optional observability samplers.

    Returns:
        Run summary with metrics and scenario metadata.

    Raises:
        GuardViolation: When safety checks fail.
    """
    LOGGER.info("run_live called scenario=%s", config.scenario)
    validate_config(config, require_marker=True)
    validate_live_prerequisites(config)
    transport = transport or build_transport("stdlib")
    metrics = MetricsCollector()
    session = ApiSession(config=config, transport=transport, metrics=metrics)
    health_attestation = session.fetch_health()
    validate_remote_attestation(health_attestation, config)
    bundle = sampler_bundle or default_sampler_bundle(
        session.fetch_health,
        database_dsn=config.database_dsn,
    )
    baseline = bundle.sample_all()
    validate_live_database_snapshot(baseline, config)
    metrics.record_sampler({"phase": "before", **baseline})
    executor = select_scenario(config.scenario)
    scenario_result = executor(session, sampler_bundle=bundle)
    after = bundle.sample_all()
    metrics.record_sampler({"phase": "after", **after})
    recovery = measure_recovery_to_baseline(bundle, baseline)
    final_snapshot = recovery.get("final_snapshot")
    if isinstance(final_snapshot, dict):
        metrics.record_sampler({"phase": "recovery", **final_snapshot})
    metrics.finalize()
    return {
        "status": "ok",
        "mode": "live",
        "config": config_log_meta(config),
        "health_attestation": {
            "status": health_attestation.get("status"),
            "database": health_attestation.get("database"),
            "environment": health_attestation.get("environment"),
            "instance_marker": health_attestation.get("instance_marker"),
            "database_name": health_attestation.get("database_name"),
        },
        "scenario": {
            "name": scenario_result.name,
            "clients_seeded": scenario_result.clients_seeded,
            "polls_executed": scenario_result.polls_executed,
            "actions_executed": scenario_result.actions_executed,
            "uploads_executed": scenario_result.uploads_executed,
            "bounded": scenario_result.bounded,
        },
        "metrics": metrics.summarize(),
        "recovery_to_baseline": recovery,
        "worker_requirements": build_worker_plan(
            config.worker_delay_s,
            config.worker_fail_rate,
        ).to_metadata(),
        "database_observability_requirements": DATABASE_OBSERVABILITY_REQUIREMENTS,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for config-check, dry-run, and live modes.

    Args:
        argv: Optional argv tail excluding the program name.

    Returns:
        Process exit code ``0`` on success, ``2`` for guard failures, ``1`` otherwise.
    """
    configure_logging()
    LOGGER.info("main called argv_provided=%s", argv is not None)
    config = load_config(argv)
    try:
        if config.config_check:
            report = run_config_check(config)
        elif config.dry_run:
            report = run_dry_run(config)
        else:
            report = run_live(config)
    except GuardViolation as exc:
        LOGGER.info("main guard_violation code=%s message=%s", exc.code, exc.message)
        print(json.dumps({"status": "error", "code": exc.code, "message": exc.message}, indent=2))
        return 2
    except Exception as exc:  # noqa: BLE001 - surfaced to operator
        LOGGER.info("main failed error=%s", exc)
        print(json.dumps({"status": "error", "message": str(exc)}, indent=2))
        return 1
    print(json.dumps(report, indent=2))
    return 0


def run_with_hooks(
    config: LoadConfig,
    *,
    transport_builder: Callable[[], HttpTransport] | None = None,
    sampler_bundle: SamplerBundle | None = None,
) -> dict[str, object]:
    """Execute using injectable transport and sampler hooks for tests.

    Args:
        config: Loaded configuration.
        transport_builder: Optional callable returning a transport.
        sampler_bundle: Optional sampler bundle.

    Returns:
        Run report from dry-run or live execution.
    """
    LOGGER.info(
        "run_with_hooks called dry_run=%s config_check=%s",
        config.dry_run,
        config.config_check,
    )
    if config.config_check:
        return run_config_check(config)
    if config.dry_run:
        transport = transport_builder() if transport_builder else RecordingTransport()
        return run_dry_run(config, transport=transport)
    transport = transport_builder() if transport_builder else build_transport("stdlib")
    return run_live(config, transport=transport, sampler_bundle=sampler_bundle)
