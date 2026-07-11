"""Postgres read adapter for admin observability endpoints.

Queries ``api_calls``, ``worker_heartbeats``, ``jobs``, and aggregate funnel
counts. This module never writes and never returns audio bytes or nicknames.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any
from uuid import UUID

import asyncpg

from app.admin_ops.config import ADMIN_WORKER_STALE_SECONDS

logger = logging.getLogger(__name__)


def _as_dict(value: Any) -> dict[str, Any]:
    """Normalize asyncpg JSONB values into plain dictionaries.

    Args:
        value: JSONB payload, string, or mapping from Postgres.

    Returns:
        A dictionary suitable for redaction and response models.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(value, memoryview):
        parsed = json.loads(bytes(value).decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    return {}


class AdminOpsRepository:
    """Read-only Postgres access for operator traces and funnel metrics."""

    def __init__(self, pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Store the shared application connection pool.

        Args:
            pool: Open asyncpg pool targeting the application database.
        """
        self._pool = pool
        logger.info("AdminOpsRepository initialized")

    async def list_api_calls(
        self,
        *,
        limit: int,
        operation: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return newest GenAI instrumentation rows.

        Args:
            limit: Maximum number of rows to return.
            operation: Optional exact operation filter (for example
                ``gauntlet_triage``).

        Returns:
            Repository-shaped call dictionaries newest-first.
        """
        logger.info(
            "list_api_calls called limit=%s operation_present=%s",
            limit,
            operation is not None,
        )
        if operation:
            rows = await self._pool.fetch(
                """
                SELECT id, model, operation, request_meta, response_meta, status,
                       latency_ms, estimated_cost_microusd, created_at
                FROM api_calls
                WHERE operation = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                operation,
                limit,
            )
        else:
            rows = await self._pool.fetch(
                """
                SELECT id, model, operation, request_meta, response_meta, status,
                       latency_ms, estimated_cost_microusd, created_at
                FROM api_calls
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
        return [
            {
                "id": row["id"],
                "model": row["model"],
                "operation": row["operation"],
                "request_meta": _as_dict(row["request_meta"]),
                "response_meta": _as_dict(row["response_meta"]),
                "status": row["status"],
                "latency_ms": row["latency_ms"],
                "estimated_cost_microusd": row["estimated_cost_microusd"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def list_worker_heartbeats(
        self,
        *,
        stale_after_seconds: float = ADMIN_WORKER_STALE_SECONDS,
    ) -> list[dict[str, Any]]:
        """Return all worker heartbeat rows with a computed healthy flag.

        Args:
            stale_after_seconds: Maximum accepted age for a running heartbeat.

        Returns:
            Worker dictionaries newest heartbeat first.
        """
        logger.info(
            "list_worker_heartbeats called stale_after_seconds=%s",
            stale_after_seconds,
        )
        rows = await self._pool.fetch(
            """
            SELECT worker_id, process_id, status, started_at, heartbeat_at, metadata,
                   (
                       status = 'running'
                       AND heartbeat_at >= now() - $1::interval
                   ) AS healthy
            FROM worker_heartbeats
            ORDER BY heartbeat_at DESC
            """,
            timedelta(seconds=stale_after_seconds),
        )
        return [
            {
                "worker_id": row["worker_id"],
                "process_id": row["process_id"],
                "status": row["status"],
                "started_at": row["started_at"],
                "heartbeat_at": row["heartbeat_at"],
                "healthy": bool(row["healthy"]),
                "metadata": _as_dict(row["metadata"]),
            }
            for row in rows
        ]

    async def list_jobs(
        self,
        *,
        limit: int,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """Return recent jobs plus aggregate status counts.

        Args:
            limit: Maximum detailed rows to return.
            status: Optional exact status filter for the detail list.

        Returns:
            A tuple of (job rows newest-first, counts_by_status).
        """
        logger.info(
            "list_jobs called limit=%s status_present=%s",
            limit,
            status is not None,
        )
        count_rows = await self._pool.fetch(
            """
            SELECT status, count(*)::int AS count
            FROM jobs
            GROUP BY status
            """
        )
        counts = {str(row["status"]): int(row["count"]) for row in count_rows}

        if status:
            rows = await self._pool.fetch(
                """
                SELECT id, kind, payload, status, tries, last_error,
                       created_at, available_at, claimed_at, completed_at
                FROM jobs
                WHERE status = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                status,
                limit,
            )
        else:
            rows = await self._pool.fetch(
                """
                SELECT id, kind, payload, status, tries, last_error,
                       created_at, available_at, claimed_at, completed_at
                FROM jobs
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )

        jobs: list[dict[str, Any]] = []
        for row in rows:
            payload = _as_dict(row["payload"])
            turn_raw = payload.get("turn_id")
            turn_id: UUID | None
            try:
                turn_id = UUID(str(turn_raw)) if turn_raw is not None else None
            except (TypeError, ValueError):
                turn_id = None
            jobs.append(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "turn_id": turn_id,
                    "status": row["status"],
                    "tries": int(row["tries"]),
                    "last_error": row["last_error"],
                    "created_at": row["created_at"],
                    "available_at": row["available_at"],
                    "claimed_at": row["claimed_at"],
                    "completed_at": row["completed_at"],
                }
            )
        return jobs, counts

    async def pipeline_funnel(self) -> dict[str, Any]:
        """Compute aggregate eligibility and job-backlog funnel counts.

        Returns:
            Dictionary matching ``AdminPipelineFunnelResponse`` fields before
            model construction.
        """
        logger.info("pipeline_funnel called")
        row = await self._pool.fetchrow(
            """
            SELECT
                (SELECT count(*)::int FROM turns WHERE outcome = 'validated')
                    AS validated_pairs,
                (SELECT count(*)::int FROM records) AS packaged_records,
                (SELECT count(*)::int FROM records WHERE training_eligible IS TRUE)
                    AS training_eligible_pairs,
                (SELECT count(*)::int FROM jobs WHERE status = 'pending')
                    AS jobs_pending,
                (SELECT count(*)::int FROM jobs WHERE status = 'processing')
                    AS jobs_processing,
                (SELECT count(*)::int FROM jobs WHERE status = 'failed')
                    AS jobs_failed
            """
        )
        assert row is not None
        validated = int(row["validated_pairs"])
        eligible = int(row["training_eligible_pairs"])
        packaged = int(row["packaged_records"])
        pass_rate = (eligible / packaged) if packaged > 0 else None
        return {
            "validated_pairs": validated,
            "packaged_records": packaged,
            "training_eligible_pairs": eligible,
            "gauntlet_pass_rate": pass_rate,
            "jobs_pending": int(row["jobs_pending"]),
            "jobs_processing": int(row["jobs_processing"]),
            "jobs_failed": int(row["jobs_failed"]),
        }
