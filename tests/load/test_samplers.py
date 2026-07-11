"""Concrete sampler mapping tests using isolated fake dependencies.

No test opens a database connection or reads host procfs. Asyncpg-shaped fakes
verify query-result mapping, and temporary proc files verify Linux resources.
"""

from __future__ import annotations

import logging
from pathlib import Path

from tools.load.samplers import (
    AsyncpgDatabaseSampler,
    AsyncpgJobSampler,
    AsyncpgSamplerBackend,
    LinuxProcSystemSampler,
)

LOGGER = logging.getLogger(__name__)


class FakeConnection:
    """Asyncpg-shaped fake returning deterministic observability rows."""

    def __init__(self) -> None:
        """Initialize query counters and close state."""
        LOGGER.info("FakeConnection.__init__ called")
        self.closed = False
        self.fetchrow_calls = 0

    async def fetchrow(self, query: str) -> dict[str, int]:
        """Return deterministic pg_stat_activity counters.

        Args:
            query: SQL query text.

        Returns:
            Activity counter mapping.
        """
        LOGGER.info("FakeConnection.fetchrow called query_length=%s", len(query))
        assert "pg_stat_activity" in query
        self.fetchrow_calls += 1
        return {
            "current_database": "dialect_factory_load_test",
            "connections_total": 12,
            "connections_active": 4,
            "connections_idle": 7,
            "connections_waiting": 1,
        }

    async def fetchval(self, query: str) -> int | float:
        """Return max connections, waiting locks, or backlog age.

        Args:
            query: SQL query text.

        Returns:
            Deterministic scalar.
        """
        LOGGER.info("FakeConnection.fetchval called query_length=%s", len(query))
        if "SHOW max_connections" in query:
            return 100
        if "pg_locks" in query:
            return 2
        assert "min(created_at)" in query
        return 8.5

    async def fetch(self, query: str) -> list[dict[str, object]]:
        """Return deterministic job status rows.

        Args:
            query: SQL query text.

        Returns:
            Pending, processing, and complete job counts.
        """
        LOGGER.info("FakeConnection.fetch called query_length=%s", len(query))
        assert "FROM jobs" in query
        return [
            {"status": "pending", "count": 9},
            {"status": "processing", "count": 2},
            {"status": "complete", "count": 40},
        ]

    async def close(self) -> None:
        """Mark the fake connection closed."""
        LOGGER.info("FakeConnection.close called")
        self.closed = True


def test_asyncpg_sampler_maps_database_and_job_metrics() -> None:
    """Map asyncpg records into stable connection, lock, and backlog fields."""
    LOGGER.info("test_asyncpg_sampler_maps_database_and_job_metrics called")
    connection = FakeConnection()
    connect_calls: list[str] = []

    async def connect(database_dsn: str) -> FakeConnection:
        """Return the shared fake connection.

        Args:
            database_dsn: Redacted test DSN.

        Returns:
            Asyncpg-shaped fake connection.
        """
        LOGGER.info("connect called dsn_length=%s", len(database_dsn))
        connect_calls.append(database_dsn)
        return connection

    backend = AsyncpgSamplerBackend(
        database_dsn=(
            "postgresql://load:redacted@127.0.0.1:5432/dialect_factory_load_test"
        ),
        connection_factory=connect,
        cache_ttl_s=60.0,
    )
    database = AsyncpgDatabaseSampler(backend).sample()
    backlog = AsyncpgJobSampler(backend).sample()

    assert database == {
        "current_database": "dialect_factory_load_test",
        "connections_total": 12,
        "connections_active": 4,
        "connections_idle": 7,
        "connections_waiting": 1,
        "max_connections": 100,
        "waiting_locks": 2,
    }
    assert backlog["jobs_pending"] == 9
    assert backlog["jobs_processing"] == 2
    assert backlog["oldest_pending_age_s"] == 8.5
    assert backlog["jobs_by_status"] == {
        "pending": 9,
        "processing": 2,
        "complete": 40,
    }
    assert len(connect_calls) == 1
    assert connection.closed is True


def test_linux_proc_sampler_maps_cpu_and_memory(tmp_path: Path) -> None:
    """Map isolated proc fixtures without psutil or real procfs reads."""
    LOGGER.info("test_linux_proc_sampler_maps_cpu_and_memory called")
    stat_path = tmp_path / "stat"
    meminfo_path = tmp_path / "meminfo"
    stat_path.write_text("cpu  100 0 50 850 0 0 0 0 0 0\n", encoding="utf-8")
    meminfo_path.write_text(
        "MemTotal:       1000 kB\nMemAvailable:    400 kB\n",
        encoding="utf-8",
    )
    sampler = LinuxProcSystemSampler(
        stat_path=stat_path,
        meminfo_path=meminfo_path,
    )
    first = sampler.sample()
    stat_path.write_text("cpu  150 0 50 900 0 0 0 0 0 0\n", encoding="utf-8")
    second = sampler.sample()

    assert first["memory_total_bytes"] == 1000 * 1024
    assert first["memory_used_bytes"] == 600 * 1024
    assert "load_harness_process_max_rss_bytes" in first
    assert "process_max_rss_bytes" not in first
    assert second["cpu_percent"] == 50.0
