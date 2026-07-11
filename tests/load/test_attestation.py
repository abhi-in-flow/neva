"""Live preflight tests for remote isolated-instance attestation.

These tests prove marker/database mismatches stop the runner after the health
probe and before joins, state polls, direct DB sampling, or scenario actions.
"""

from __future__ import annotations

import json
import logging

import pytest

from tools.load.config import LoadConfig
from tools.load.guards import GuardViolation
from tools.load.runner import run_live
from tools.load.transport import RecordingTransport

LOGGER = logging.getLogger(__name__)


class AttestationTransport(RecordingTransport):
    """Recording transport with an operator-selected health attestation."""

    def __init__(
        self,
        *,
        marker: str,
        database_name: str,
        environment: str = "load-test",
    ) -> None:
        """Initialize canned attestation values.

        Args:
            marker: Remote instance marker returned by health.
            database_name: Remote database name returned by health.
            environment: Remote environment attestation.
        """
        LOGGER.info(
            "AttestationTransport.__init__ called marker_len=%s database_name=%s",
            len(marker),
            database_name,
        )
        super().__init__()
        self.marker = marker
        self.database_name = database_name
        self.environment = environment

    def _canned_body(self, method: str, url: str) -> bytes:
        """Return custom health JSON and delegate all other endpoint fixtures.

        Args:
            method: HTTP verb.
            url: Absolute request URL.

        Returns:
            Canned JSON response bytes.
        """
        LOGGER.info(
            "AttestationTransport._canned_body called method=%s url_length=%s",
            method,
            len(url),
        )
        if url.endswith("/api/health"):
            return json.dumps(
                {
                    "status": "ok",
                    "database": "connected",
                    "environment": self.environment,
                    "instance_marker": self.marker,
                    "database_name": self.database_name,
                }
            ).encode("utf-8")
        return super()._canned_body(method, url)


def _live_config(safe_config: LoadConfig) -> LoadConfig:
    """Return a live-mode config with a guarded direct DB sampler.

    Args:
        safe_config: Base guarded test configuration.

    Returns:
        Configuration suitable for remote-attestation tests.
    """
    LOGGER.info("_live_config called")
    return LoadConfig(
        **{
            **safe_config.__dict__,
            "dry_run": False,
            "scenario": "action_burst",
        }
    )


@pytest.mark.parametrize(
    ("environment", "marker", "database_name", "expected_code"),
    [
        (
            "integration",
            "wave2-load-isolated",
            "dialect_factory_load_test",
            "remote_environment_mismatch",
        ),
        (
            "load-test",
            "wrong-marker",
            "dialect_factory_load_test",
            "remote_marker_mismatch",
        ),
        (
            "load-test",
            "wave2-load-isolated",
            "dialect_factory",
            "remote_database_mismatch",
        ),
    ],
)
def test_remote_attestation_refuses_before_scenario_traffic(
    safe_config: LoadConfig,
    environment: str,
    marker: str,
    database_name: str,
    expected_code: str,
) -> None:
    """Stop after health when the remote instance does not attest correctly."""
    LOGGER.info(
        "test_remote_attestation_refuses_before_scenario_traffic called expected_code=%s",
        expected_code,
    )
    transport = AttestationTransport(
        marker=marker,
        database_name=database_name,
        environment=environment,
    )
    with pytest.raises(GuardViolation) as exc:
        run_live(_live_config(safe_config), transport=transport)
    assert exc.value.code == expected_code
    assert [call["url"] for call in transport.calls] == [
        "http://127.0.0.1:8000/api/health"
    ]


class StableSamplerBundle:
    """Sampler bundle fake that is immediately at baseline."""

    def sample_all(self) -> dict[str, object]:
        """Return a stable healthy snapshot.

        Returns:
            Database, backlog, system, and health sections.
        """
        LOGGER.info("StableSamplerBundle.sample_all called")
        return {
            "timestamp": 1.0,
            "database": {
                "current_database": "dialect_factory_load_test",
                "connections_active": 1,
                "connections_waiting": 0,
                "waiting_locks": 0,
            },
            "backlog": {"jobs_pending": 0, "jobs_processing": 0},
            "system": {"cpu_percent": 1.0},
            "recovery": {"ok": True},
        }


def test_live_mode_wires_guarded_database_dsn(
    safe_config: LoadConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass the guarded DSN into automatic live sampler construction."""
    LOGGER.info("test_live_mode_wires_guarded_database_dsn called")
    captured: dict[str, object] = {}

    def build_bundle(fetch_health, database_dsn=None):
        """Capture sampler-construction inputs and return a stable fake.

        Args:
            fetch_health: Injected API health callable.
            database_dsn: Guarded DSN supplied by the runner.

        Returns:
            Stable fake sampler bundle.
        """
        LOGGER.info("build_bundle called database_dsn_set=%s", bool(database_dsn))
        captured["fetch_health"] = fetch_health
        captured["database_dsn"] = database_dsn
        return StableSamplerBundle()

    monkeypatch.setattr("tools.load.runner.default_sampler_bundle", build_bundle)
    config = _live_config(safe_config)
    transport = AttestationTransport(
        marker=config.isolated_marker,
        database_name=config.database_name,
    )
    report = run_live(config, transport=transport)

    assert captured["database_dsn"] == safe_config.database_dsn
    assert report["health_attestation"]["database_name"] == config.database_name


def test_live_mode_requires_database_dsn_before_http(
    safe_config: LoadConfig,
) -> None:
    """Reject measured live mode without a DSN before the health request."""
    LOGGER.info("test_live_mode_requires_database_dsn_before_http called")
    config = LoadConfig(
        **{
            **_live_config(safe_config).__dict__,
            "database_dsn": None,
        }
    )
    transport = AttestationTransport(
        marker=config.isolated_marker,
        database_name=config.database_name,
    )
    with pytest.raises(GuardViolation) as exc:
        run_live(config, transport=transport)
    assert exc.value.code == "live_database_dsn_required"
    assert transport.calls == []


class UnavailableDatabaseSamplerBundle(StableSamplerBundle):
    """Sampler bundle fake whose direct database probe failed."""

    def sample_all(self) -> dict[str, object]:
        """Return an unavailable direct database snapshot.

        Returns:
            Snapshot carrying an explicit database sampler error.
        """
        LOGGER.info("UnavailableDatabaseSamplerBundle.sample_all called")
        snapshot = super().sample_all()
        snapshot["database"] = {
            "database_sampler": "unavailable",
            "error": "connection refused",
        }
        return snapshot


def test_live_mode_refuses_unavailable_database_before_scenario(
    safe_config: LoadConfig,
) -> None:
    """Reject a measured run when direct database sampling is unavailable."""
    LOGGER.info("test_live_mode_refuses_unavailable_database_before_scenario called")
    config = _live_config(safe_config)
    transport = AttestationTransport(
        marker=config.isolated_marker,
        database_name=config.database_name,
    )
    with pytest.raises(GuardViolation) as exc:
        run_live(
            config,
            transport=transport,
            sampler_bundle=UnavailableDatabaseSamplerBundle(),
        )
    assert exc.value.code == "live_database_sampler_unavailable"
    assert [call["url"] for call in transport.calls] == [
        "http://127.0.0.1:8000/api/health"
    ]


class WrongDatabaseSamplerBundle(StableSamplerBundle):
    """Sampler bundle fake connected to the wrong database."""

    def sample_all(self) -> dict[str, object]:
        """Return a mismatched current-database attestation.

        Returns:
            Otherwise healthy snapshot with the wrong database name.
        """
        LOGGER.info("WrongDatabaseSamplerBundle.sample_all called")
        snapshot = super().sample_all()
        database = snapshot["database"]
        assert isinstance(database, dict)
        database["current_database"] = "dialect_factory"
        return snapshot


def test_live_mode_refuses_wrong_direct_database_before_scenario(
    safe_config: LoadConfig,
) -> None:
    """Reject direct sampling connected to a different database."""
    LOGGER.info("test_live_mode_refuses_wrong_direct_database_before_scenario called")
    config = _live_config(safe_config)
    transport = AttestationTransport(
        marker=config.isolated_marker,
        database_name=config.database_name,
    )
    with pytest.raises(GuardViolation) as exc:
        run_live(
            config,
            transport=transport,
            sampler_bundle=WrongDatabaseSamplerBundle(),
        )
    assert exc.value.code == "live_database_attestation_mismatch"
