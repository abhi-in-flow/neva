"""Injectable observability samplers for load runs.

Provides direct asyncpg sampling for database activity, locks, and worker
backlog, plus stdlib/Linux procfs CPU and memory metrics and bounded post-run
recovery checks. Asyncpg is imported lazily only for guarded live runs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import resource
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Protocol

LOGGER = logging.getLogger(__name__)

DATABASE_SAMPLE_CACHE_S = 0.05
PROC_STAT_PATH = Path("/proc/stat")
PROC_MEMINFO_PATH = Path("/proc/meminfo")
RECOVERY_TIMEOUT_S = 5.0
RECOVERY_SAMPLE_INTERVAL_S = 0.25
RECOVERY_ACTIVE_CONNECTION_TOLERANCE = 1


class DatabaseSampler(Protocol):
    """Sample database connectivity and contention signals."""

    def sample(self) -> dict[str, object]:
        """Return one redacted database snapshot."""
        ...


class BacklogSampler(Protocol):
    """Sample worker backlog depth."""

    def sample(self) -> dict[str, object]:
        """Return pending job counts or backlog metadata."""
        ...


class SystemSampler(Protocol):
    """Sample host CPU and memory utilization."""

    def sample(self) -> dict[str, object]:
        """Return host resource snapshot."""
        ...


@dataclass
class NullDatabaseSampler:
    """No-op database sampler used for dry-run and unit tests."""

    def sample(self) -> dict[str, object]:
        """Return an explicit unavailable snapshot.

        Returns:
            Snapshot indicating database sampling was not configured.
        """
        LOGGER.info("NullDatabaseSampler.sample called")
        return {"database_sampler": "unavailable"}


@dataclass
class NullBacklogSampler:
    """No-op backlog sampler."""

    def sample(self) -> dict[str, object]:
        """Return an explicit unavailable backlog snapshot."""
        LOGGER.info("NullBacklogSampler.sample called")
        return {"backlog_sampler": "unavailable"}


@dataclass
class NullSystemSampler:
    """No-op system sampler."""

    def sample(self) -> dict[str, object]:
        """Return an explicit unavailable system snapshot."""
        LOGGER.info("NullSystemSampler.sample called")
        return {"system_sampler": "unavailable"}


def _row_value(row: object, key: str, default: object = 0) -> object:
    """Read a value from an asyncpg record, mapping, or attribute object.

    Args:
        row: Query result row.
        key: Column name to retrieve.
        default: Value returned when the column is unavailable.

    Returns:
        Extracted value or ``default``.
    """
    LOGGER.info("_row_value called key=%s row_type=%s", key, type(row).__name__)
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, TypeError):
        return getattr(row, key, default)


async def _asyncpg_connect(database_dsn: str) -> object:
    """Open an asyncpg connection without importing it during dry runs.

    Args:
        database_dsn: Guarded Postgres DSN.

    Returns:
        Connected asyncpg connection.

    Side effects:
        Imports asyncpg and opens one database connection.
    """
    LOGGER.info("_asyncpg_connect called dsn_length=%s", len(database_dsn))
    import asyncpg

    return await asyncpg.connect(database_dsn)


@dataclass
class AsyncpgSamplerBackend:
    """Collect database activity, lock, and job-backlog metrics via asyncpg."""

    database_dsn: str
    connection_factory: Callable[[str], Awaitable[object]] = _asyncpg_connect
    cache_ttl_s: float = DATABASE_SAMPLE_CACHE_S

    def __post_init__(self) -> None:
        """Initialize the bounded shared cache used by section samplers."""
        LOGGER.info(
            "AsyncpgSamplerBackend.__post_init__ called dsn_length=%s cache_ttl_s=%s",
            len(self.database_dsn),
            self.cache_ttl_s,
        )
        self._cache: dict[str, object] | None = None
        self._cache_at = 0.0
        self._lock = threading.Lock()

    async def _collect_async(self) -> dict[str, object]:
        """Collect one direct database snapshot.

        Returns:
            Activity, max-connection, lock, and job-status metrics.

        Side effects:
            Opens and closes one guarded Postgres connection.
        """
        LOGGER.info("AsyncpgSamplerBackend._collect_async called")
        connection = await self.connection_factory(self.database_dsn)
        try:
            activity = await connection.fetchrow(  # type: ignore[attr-defined]
                """
                SELECT
                    current_database() AS current_database,
                    count(*)::int AS connections_total,
                    count(*) FILTER (WHERE state = 'active')::int AS connections_active,
                    count(*) FILTER (WHERE state = 'idle')::int AS connections_idle,
                    count(*) FILTER (WHERE wait_event IS NOT NULL)::int AS connections_waiting
                FROM pg_stat_activity
                WHERE datname = current_database()
                """
            )
            try:
                max_connections = int(
                    await connection.fetchval("SHOW max_connections")  # type: ignore[attr-defined]
                )
            except Exception as exc:  # noqa: BLE001 - privilege/version dependent
                LOGGER.info("max_connections unavailable error=%s", exc)
                max_connections = None
            try:
                waiting_locks = int(
                    await connection.fetchval(  # type: ignore[attr-defined]
                        "SELECT count(*)::int FROM pg_locks WHERE NOT granted"
                    )
                )
            except Exception as exc:  # noqa: BLE001 - privilege/version dependent
                LOGGER.info("waiting lock count unavailable error=%s", exc)
                waiting_locks = None
            job_rows = await connection.fetch(  # type: ignore[attr-defined]
                "SELECT status, count(*)::int AS count FROM jobs GROUP BY status"
            )
            jobs_by_status = {
                str(_row_value(row, "status", "unknown")): int(_row_value(row, "count", 0))
                for row in job_rows
            }
            try:
                oldest_pending_age_s = await connection.fetchval(  # type: ignore[attr-defined]
                    """
                    SELECT COALESCE(
                        EXTRACT(EPOCH FROM (now() - min(created_at))),
                        0
                    )::double precision
                    FROM jobs
                    WHERE status = 'pending'
                    """
                )
            except Exception as exc:  # noqa: BLE001 - schema/privilege dependent
                LOGGER.info("oldest pending age unavailable error=%s", exc)
                oldest_pending_age_s = None
            return {
                "database": {
                    "current_database": str(
                        _row_value(activity, "current_database", "")
                    ),
                    "connections_total": int(_row_value(activity, "connections_total", 0)),
                    "connections_active": int(_row_value(activity, "connections_active", 0)),
                    "connections_idle": int(_row_value(activity, "connections_idle", 0)),
                    "connections_waiting": int(_row_value(activity, "connections_waiting", 0)),
                    "max_connections": max_connections,
                    "waiting_locks": waiting_locks,
                },
                "backlog": {
                    "jobs_by_status": jobs_by_status,
                    "jobs_pending": jobs_by_status.get("pending", 0),
                    "jobs_processing": jobs_by_status.get("processing", 0),
                    "oldest_pending_age_s": (
                        None
                        if oldest_pending_age_s is None
                        else float(oldest_pending_age_s)
                    ),
                },
            }
        finally:
            await connection.close()  # type: ignore[attr-defined]

    def collect(self) -> dict[str, object]:
        """Return a cached or fresh database/job snapshot.

        Returns:
            Combined database and backlog sections. Connection failures map to
            explicit unavailable metadata and do not abort the load scenario.
        """
        LOGGER.info("AsyncpgSamplerBackend.collect called")
        with self._lock:
            now = time.monotonic()
            if self._cache is not None and now - self._cache_at <= self.cache_ttl_s:
                return self._cache
            try:
                snapshot = asyncio.run(self._collect_async())
            except Exception as exc:  # noqa: BLE001 - sampling is best effort
                LOGGER.info("database sampler unavailable error=%s", exc)
                snapshot = {
                    "database": {"database_sampler": "unavailable", "error": str(exc)},
                    "backlog": {"backlog_sampler": "unavailable", "error": str(exc)},
                }
            self._cache = snapshot
            self._cache_at = now
            return snapshot


@dataclass
class AsyncpgDatabaseSampler:
    """Expose the database section from a shared asyncpg sampler backend."""

    backend: AsyncpgSamplerBackend

    def sample(self) -> dict[str, object]:
        """Return connection and lock measurements.

        Returns:
            Database section from the shared backend snapshot.
        """
        LOGGER.info("AsyncpgDatabaseSampler.sample called")
        return dict(self.backend.collect()["database"])  # type: ignore[arg-type]


@dataclass
class AsyncpgJobSampler:
    """Expose job backlog measurements from a shared asyncpg sampler backend."""

    backend: AsyncpgSamplerBackend

    def sample(self) -> dict[str, object]:
        """Return jobs by status, pending depth, and oldest pending age.

        Returns:
            Backlog section from the shared backend snapshot.
        """
        LOGGER.info("AsyncpgJobSampler.sample called")
        return dict(self.backend.collect()["backlog"])  # type: ignore[arg-type]


@dataclass
class LinuxProcSystemSampler:
    """Measure Linux load, process CPU, and memory without optional packages."""

    stat_path: Path = PROC_STAT_PATH
    meminfo_path: Path = PROC_MEMINFO_PATH

    def __post_init__(self) -> None:
        """Initialize CPU counters used to calculate utilization deltas."""
        LOGGER.info(
            "LinuxProcSystemSampler.__post_init__ called stat_path=%s meminfo_path=%s",
            self.stat_path,
            self.meminfo_path,
        )
        self._previous_cpu: tuple[int, int] | None = None

    def _read_cpu(self) -> tuple[int, int] | None:
        """Read aggregate CPU total and idle ticks from ``/proc/stat``.

        Returns:
            ``(total_ticks, idle_ticks)`` or ``None`` when unavailable.

        Side effects:
            Reads one Linux procfs file.
        """
        LOGGER.info("LinuxProcSystemSampler._read_cpu called path=%s", self.stat_path)
        try:
            first_line = self.stat_path.read_text(encoding="utf-8").splitlines()[0]
            values = [int(value) for value in first_line.split()[1:]]
        except (OSError, ValueError, IndexError):
            return None
        if len(values) < 4:
            return None
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values), idle

    def _read_memory(self) -> dict[str, object]:
        """Read host memory totals from ``/proc/meminfo``.

        Returns:
            Total, available, used bytes, and usage percentage when available.

        Side effects:
            Reads one Linux procfs file.
        """
        LOGGER.info("LinuxProcSystemSampler._read_memory called path=%s", self.meminfo_path)
        try:
            rows = self.meminfo_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            return {"memory_sampler": "unavailable", "error": str(exc)}
        values: dict[str, int] = {}
        for row in rows:
            key, _, raw = row.partition(":")
            parts = raw.strip().split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0]) * 1024
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", values.get("MemFree", 0))
        used = max(0, total - available)
        return {
            "memory_total_bytes": total,
            "memory_available_bytes": available,
            "memory_used_bytes": used,
            "memory_used_percent": (used / total * 100.0) if total else None,
        }

    def sample(self) -> dict[str, object]:
        """Return Linux CPU/load and host/process memory measurements.

        Returns:
            JSON-serializable resource snapshot.

        Side effects:
            Reads procfs and process resource usage.
        """
        LOGGER.info("LinuxProcSystemSampler.sample called")
        cpu = self._read_cpu()
        cpu_percent: float | None = None
        if cpu is not None and self._previous_cpu is not None:
            total_delta = cpu[0] - self._previous_cpu[0]
            idle_delta = cpu[1] - self._previous_cpu[1]
            if total_delta > 0:
                cpu_percent = (1.0 - idle_delta / total_delta) * 100.0
        if cpu is not None:
            self._previous_cpu = cpu
        try:
            load_1m, load_5m, load_15m = os.getloadavg()
        except OSError:
            load_1m = load_5m = load_15m = None
        max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        load_harness_process_max_rss_bytes = max_rss * 1024
        return {
            "cpu_percent": cpu_percent,
            "load_1m": load_1m,
            "load_5m": load_5m,
            "load_15m": load_15m,
            "cpu_count": os.cpu_count(),
            "load_harness_process_max_rss_bytes": load_harness_process_max_rss_bytes,
            **self._read_memory(),
        }


@dataclass
class HealthRecoverySampler:
    """Use API health checks as a lightweight recovery probe."""

    fetch_health: Callable[[], dict[str, object]]

    def sample(self) -> dict[str, object]:
        """Fetch health and classify recovery readiness.

        Returns:
            Snapshot with health status and timestamp.

        Side effects:
            Invokes the injected health fetch callable (HTTP in live mode).
        """
        LOGGER.info("HealthRecoverySampler.sample called")
        started = time.perf_counter()
        try:
            payload = self.fetch_health()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return {
                "recovery_probe": "health",
                "status": payload.get("status"),
                "database": payload.get("database"),
                "environment": payload.get("environment"),
                "instance_marker": payload.get("instance_marker"),
                "database_name": payload.get("database_name"),
                "latency_ms": elapsed_ms,
                "ok": payload.get("status") == "ok",
            }
        except Exception as exc:  # noqa: BLE001 - sampler must not crash runner
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            LOGGER.info("HealthRecoverySampler.sample failed error=%s", exc)
            return {
                "recovery_probe": "health",
                "ok": False,
                "error": str(exc),
                "latency_ms": elapsed_ms,
            }


@dataclass
class CallableDatabaseSampler:
    """Wrap an injected callable that returns database pool metadata."""

    fn: Callable[[], dict[str, object]]

    def sample(self) -> dict[str, object]:
        """Invoke the injected database sampler.

        Returns:
            Redacted database snapshot from the callable.
        """
        LOGGER.info("CallableDatabaseSampler.sample called")
        return {"database": self.fn()}


@dataclass
class CallableBacklogSampler:
    """Wrap an injected callable that returns worker backlog metadata."""

    fn: Callable[[], dict[str, object]]

    def sample(self) -> dict[str, object]:
        """Invoke the injected backlog sampler.

        Returns:
            Backlog snapshot from the callable.
        """
        LOGGER.info("CallableBacklogSampler.sample called")
        return {"backlog": self.fn()}


@dataclass
class CallableSystemSampler:
    """Wrap an injected callable that returns CPU/RAM metadata."""

    fn: Callable[[], dict[str, object]]

    def sample(self) -> dict[str, object]:
        """Invoke the injected system sampler.

        Returns:
            Host resource snapshot from the callable.
        """
        LOGGER.info("CallableSystemSampler.sample called")
        return {"system": self.fn()}


@dataclass
class SamplerBundle:
    """Group observability samplers used during a run."""

    database: DatabaseSampler
    backlog: BacklogSampler
    system: SystemSampler
    recovery: HealthRecoverySampler

    def sample_all(self) -> dict[str, object]:
        """Collect all sampler outputs into one snapshot.

        Returns:
            Combined redacted observability dict.
        """
        LOGGER.info("SamplerBundle.sample_all called")
        return {
            "timestamp": time.time(),
            "database": self.database.sample(),
            "backlog": self.backlog.sample(),
            "system": self.system.sample(),
            "recovery": self.recovery.sample(),
        }


def _numeric_metric(
    snapshot: dict[str, object],
    section: str,
    key: str,
) -> float | None:
    """Extract a numeric metric from one sampler snapshot.

    Args:
        snapshot: Combined sampler snapshot.
        section: Top-level sampler section.
        key: Numeric metric key.

    Returns:
        Float value or ``None`` when unavailable.
    """
    LOGGER.info("_numeric_metric called section=%s key=%s", section, key)
    values = snapshot.get(section)
    if not isinstance(values, Mapping):
        return None
    value = values.get(key)
    if isinstance(value, int | float):
        return float(value)
    return None


def is_recovered_to_baseline(
    baseline: dict[str, object],
    current: dict[str, object],
) -> bool:
    """Determine whether DB contention and backlog returned to baseline.

    Args:
        baseline: Snapshot captured before scenario traffic.
        current: Snapshot captured after scenario traffic.

    Returns:
        ``True`` when available DB/backlog signals are no worse than baseline
        and the API health probe is ready.
    """
    LOGGER.info("is_recovered_to_baseline called")
    recovery = current.get("recovery")
    if isinstance(recovery, Mapping) and recovery.get("ok") is False:
        return False
    comparisons = (
        ("database", "waiting_locks", 0),
        ("database", "connections_waiting", 0),
        ("backlog", "jobs_pending", 0),
        ("backlog", "jobs_processing", 0),
        (
            "database",
            "connections_active",
            RECOVERY_ACTIVE_CONNECTION_TOLERANCE,
        ),
    )
    compared = False
    for section, key, tolerance in comparisons:
        before = _numeric_metric(baseline, section, key)
        after = _numeric_metric(current, section, key)
        if before is None or after is None:
            continue
        compared = True
        if after > before + tolerance:
            return False
    return compared or bool(isinstance(recovery, Mapping) and recovery.get("ok"))


def measure_recovery_to_baseline(
    bundle: SamplerBundle,
    baseline: dict[str, object],
    *,
    timeout_s: float = RECOVERY_TIMEOUT_S,
    interval_s: float = RECOVERY_SAMPLE_INTERVAL_S,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Measure bounded time for observable signals to return to baseline.

    Args:
        bundle: Live sampler bundle.
        baseline: Pre-scenario sampler snapshot.
        timeout_s: Maximum recovery observation window.
        interval_s: Delay between bounded recovery samples.
        sleep: Injectable sleep function for tests.

    Returns:
        Recovery status, elapsed seconds, number of samples, and final snapshot.
    """
    LOGGER.info(
        "measure_recovery_to_baseline called timeout_s=%s interval_s=%s",
        timeout_s,
        interval_s,
    )
    started = time.monotonic()
    sample_count = 0
    latest: dict[str, object] = {}
    while True:
        latest = bundle.sample_all()
        sample_count += 1
        elapsed_s = time.monotonic() - started
        if is_recovered_to_baseline(baseline, latest):
            return {
                "recovered": True,
                "elapsed_s": elapsed_s,
                "sample_count": sample_count,
                "final_snapshot": latest,
            }
        if elapsed_s >= timeout_s:
            return {
                "recovered": False,
                "elapsed_s": elapsed_s,
                "sample_count": sample_count,
                "final_snapshot": latest,
            }
        sleep(min(interval_s, max(0.0, timeout_s - elapsed_s)))


def default_sampler_bundle(
    fetch_health: Callable[[], dict[str, object]],
    database_dsn: str | None = None,
) -> SamplerBundle:
    """Build concrete live samplers, with direct DB sampling when configured.

    Args:
        fetch_health: Callable used by the recovery sampler.
        database_dsn: Guarded Postgres DSN, or ``None`` to report DB metrics
            unavailable.

    Returns:
        Sampler bundle with Linux resource metrics and optional asyncpg DB/job
        measurements.
    """
    LOGGER.info("default_sampler_bundle called database_dsn_set=%s", bool(database_dsn))
    if database_dsn:
        backend = AsyncpgSamplerBackend(database_dsn=database_dsn)
        database: DatabaseSampler = AsyncpgDatabaseSampler(backend)
        backlog: BacklogSampler = AsyncpgJobSampler(backend)
    else:
        database = NullDatabaseSampler()
        backlog = NullBacklogSampler()
    return SamplerBundle(
        database=database,
        backlog=backlog,
        system=LinuxProcSystemSampler(),
        recovery=HealthRecoverySampler(fetch_health=fetch_health),
    )
