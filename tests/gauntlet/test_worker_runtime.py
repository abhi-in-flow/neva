"""Worker runtime tests for bounded pools, heartbeat lifecycle, and health.

All collaborators are in-memory fakes. No Postgres, Gemini, runtime files, or
real process signals are used.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

import worker.__main__ as worker_main
import worker.health as worker_health
from worker.config import GauntletLimits, WorkerSettings
from worker.repository import GauntletRepository, WorkerHeartbeatHealth
from worker.service import GauntletService


def _settings(**overrides: object) -> WorkerSettings:
    """Build isolated worker settings without reading the repository env file."""
    values: dict[str, object] = {
        "database_url": "postgresql://safe:redacted@localhost/test",
        "data_dir": Path("/tmp/isolated-worker-test"),
        "worker_id": "wave2-worker-1",
        "worker_pool_min_size": 2,
        "worker_pool_max_size": 5,
        "heartbeat_interval_seconds": 4,
        "heartbeat_stale_seconds": 20,
        "app_environment": "test",
    }
    values.update(overrides)
    return WorkerSettings(_env_file=None, **values)


class FakePool:
    """Minimal pool with close tracking for entrypoint tests."""

    def __init__(self) -> None:
        """Initialize open state."""
        self.closed = False

    async def close(self) -> None:
        """Record pool closure."""
        self.closed = True


class FakeRepository:
    """Capture lifecycle heartbeats and stale-recovery calls."""

    instances: list["FakeRepository"] = []

    def __init__(self, pool: object, **kwargs: object) -> None:
        """Capture constructor arguments used by the entrypoint."""
        self.pool = pool
        self.kwargs = kwargs
        self.heartbeats: list[tuple[str, int, str, dict[str, object]]] = []
        self.recover_calls: list[float] = []
        self.__class__.instances.append(self)

    async def upsert_worker_heartbeat(
        self,
        *,
        worker_id: str,
        process_id: int,
        status: str,
        metadata: dict[str, object],
    ) -> None:
        """Capture one lifecycle heartbeat."""
        self.heartbeats.append((worker_id, process_id, status, metadata))


class FakeClient:
    """No-cost client with close tracking."""

    def __init__(self) -> None:
        """Initialize open state."""
        self.closed = False

    async def aclose(self) -> None:
        """Record client closure."""
        self.closed = True


class FakeService:
    """Capture entrypoint recovery and run-mode dispatch."""

    instances: list["FakeService"] = []

    def __init__(self, repository: FakeRepository, client: FakeClient, *args: Any, **kwargs: Any) -> None:
        """Store collaborators and constructor configuration."""
        self.repository = repository
        self.client = client
        self.args = args
        self.kwargs = kwargs
        self.recovered = 0
        self.processed = 0
        self.ran_forever = 0
        self.__class__.instances.append(self)

    async def recover_stale_claims(self) -> int:
        """Record the single entrypoint startup recovery."""
        self.recovered += 1
        return 0

    async def process_once(self) -> bool:
        """Record once-mode processing."""
        self.processed += 1
        return False

    async def run_forever(self) -> None:
        """Record continuous-mode dispatch and return for the test."""
        self.ran_forever += 1


@pytest.mark.asyncio
async def test_entrypoint_bounds_pool_and_writes_heartbeat_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entrypoint uses configured pool bounds and starting/running/stopping."""
    FakeRepository.instances.clear()
    FakeService.instances.clear()
    pool = FakePool()
    client = FakeClient()
    create_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr(worker_main.asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(worker_main, "GauntletRepository", FakeRepository)
    monkeypatch.setattr(worker_main, "GauntletService", FakeService)
    monkeypatch.setattr(worker_main, "create_configured_triage_client", lambda settings, pool: client)
    settings = _settings()

    await worker_main._run(settings, once=True)

    create_pool.assert_awaited_once_with(
        settings.database_url,
        min_size=2,
        max_size=5,
    )
    repository = FakeRepository.instances[-1]
    assert [heartbeat[2] for heartbeat in repository.heartbeats] == [
        "starting",
        "running",
        "stopping",
    ]
    service = FakeService.instances[-1]
    assert service.recovered == 1
    assert service.processed == 1
    assert service.ran_forever == 0
    assert service.kwargs["heartbeat_interval_seconds"] == 4
    assert client.closed is True
    assert pool.closed is True


@pytest.mark.asyncio
async def test_service_heartbeats_while_job_processing_is_busy(tmp_path: Path) -> None:
    """Independent heartbeat task updates while ``process_once`` is blocked."""

    class BusyRepository:
        """Capture heartbeat calls required by the service loop."""

        def __init__(self) -> None:
            """Initialize heartbeat capture."""
            self.heartbeats: list[str] = []

        async def upsert_worker_heartbeat(self, **kwargs: object) -> None:
            """Capture heartbeat status."""
            self.heartbeats.append(str(kwargs["status"]))

    repository = BusyRepository()
    service = GauntletService(
        repository,  # type: ignore[arg-type]
        AsyncMock(),
        tmp_path,
        GauntletLimits(stale_claim_interval_seconds=100),
        worker_id="busy-worker",
        heartbeat_interval_seconds=0.01,
    )
    blocker = asyncio.Event()
    service.process_once = AsyncMock(side_effect=blocker.wait)  # type: ignore[method-assign]
    task = asyncio.create_task(service.run_forever())
    try:
        for _ in range(100):
            if repository.heartbeats:
                break
            await asyncio.sleep(0.005)
        assert repository.heartbeats == ["running"]
    finally:
        blocker.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
@pytest.mark.parametrize(("healthy", "expected"), [(True, 0), (False, 1)])
async def test_health_exit_code_reflects_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    healthy: bool,
    expected: int,
) -> None:
    """Health CLI exits nonzero for missing, stale, or non-running state."""

    async def fake_read(settings: WorkerSettings) -> WorkerHeartbeatHealth:
        """Return requested test heartbeat state."""
        return WorkerHeartbeatHealth(
            worker_id=settings.worker_id,
            exists=healthy,
            status="running" if healthy else "stopping",
            heartbeat_at=None,
            healthy=healthy,
        )

    monkeypatch.setattr(worker_health, "read_worker_health", fake_read)
    assert await worker_health.health_exit_code(_settings()) == expected


def test_repository_health_requires_running_recent_heartbeat() -> None:
    """Repository health SQL rejects non-running and stale heartbeat rows."""
    source = inspect.getsource(GauntletRepository.get_worker_heartbeat_health)
    assert "status = 'running'" in source
    assert "heartbeat_at >= now() - $2::interval" in source
    assert "timedelta(seconds=stale_after_seconds)" in source


def test_worker_settings_reject_invalid_pool_and_heartbeat_bounds() -> None:
    """Operational settings reject unsafe min/max and stale relationships."""
    with pytest.raises(ValidationError, match="worker_pool_min_size"):
        _settings(worker_pool_min_size=6, worker_pool_max_size=5)
    with pytest.raises(ValidationError, match="heartbeat_stale_seconds"):
        _settings(heartbeat_interval_seconds=20, heartbeat_stale_seconds=20)
