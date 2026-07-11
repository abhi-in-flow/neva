"""Safety and deterministic behavior tests for isolated-load fake triage.

Tests inject a recording sleeper, so artificial delay coverage never waits in
real time and no Gemini or database client is constructed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from worker.config import WorkerSettings
from worker.fake_triage import create_fake_triage_client, validate_fake_mode


def _settings(**overrides: object) -> WorkerSettings:
    """Build environment-independent worker settings for fake-mode tests."""
    values: dict[str, object] = {
        "app_environment": "development",
        "instance_marker": "",
        "worker_fake_gemini": False,
        "worker_id": "test-worker",
    }
    values.update(overrides)
    return WorkerSettings(_env_file=None, **values)


@pytest.mark.parametrize(
    ("app_environment", "instance_marker", "fake_enabled"),
    [
        ("production", "", True),
        ("load-test", "", True),
        ("production", "wave2-load-isolated", True),
        ("load-test", "wave2-load-isolated", False),
    ],
)
def test_fake_mode_refuses_partial_or_unsafe_gates(
    app_environment: str,
    instance_marker: str,
    fake_enabled: bool,
) -> None:
    """Every partial fake/load configuration must fail closed."""
    settings = _settings(
        app_environment=app_environment,
        instance_marker=instance_marker,
        worker_fake_gemini=fake_enabled,
    )
    with pytest.raises(RuntimeError):
        validate_fake_mode(settings)


def test_normal_production_mode_does_not_select_fake() -> None:
    """Production with fake disabled remains valid and selects paid adapter."""
    assert validate_fake_mode(_settings(app_environment="production")) is False


@pytest.mark.asyncio
async def test_fake_delay_and_clean_output_without_real_sleep(tmp_path: Path) -> None:
    """Injected sleeper receives delay while result stays deterministic and clean."""
    delays: list[float] = []

    async def record_sleep(seconds: float) -> None:
        """Record requested fake delay without sleeping."""
        delays.append(seconds)

    settings = _settings(
        app_environment="load-test",
        instance_marker="wave2-load-isolated",
        worker_fake_gemini=True,
        worker_fake_gemini_delay_seconds=2.5,
        worker_fake_gemini_failure_rate=0.0,
    )
    client = create_fake_triage_client(settings, sleep=record_sleep)
    result = await client.triage_audio(
        model="unused-model",
        prompt="fixture prompt",
        response_schema={"type": "object"},
        audio_path=tmp_path / "clean.flac",
        thinking_level="low",
    )

    assert delays == [2.5]
    assert result["is_speech"] is True
    assert result["single_speaker"] is True
    assert result["audio_quality_ok"] is True
    assert result["is_label_readout"] is False


@pytest.mark.asyncio
async def test_fake_failure_is_deterministic_without_real_sleep(tmp_path: Path) -> None:
    """Failure rate one must fail every request after the injected no-op delay."""

    async def no_sleep(seconds: float) -> None:
        """Accept artificial delay without waiting."""
        assert seconds == 1.0

    settings = _settings(
        app_environment="load-test",
        instance_marker="wave2-load-isolated",
        worker_fake_gemini=True,
        worker_fake_gemini_delay_seconds=1.0,
        worker_fake_gemini_failure_rate=1.0,
    )
    client = create_fake_triage_client(settings, sleep=no_sleep)

    with pytest.raises(RuntimeError, match="deterministic failure"):
        await client.triage_audio(
            model="unused-model",
            prompt="fixture prompt",
            response_schema={"type": "object"},
            audio_path=tmp_path / "failure.flac",
            thinking_level="low",
        )
