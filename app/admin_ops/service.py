"""Service layer for demo-grade admin observability reads.

Transforms repository rows into frozen contract models and applies prompt
redaction before any metadata reaches the browser.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.admin_ops.config import (
    ADMIN_API_CALL_DEFAULT_LIMIT,
    ADMIN_API_CALL_LIST_LIMIT,
    ADMIN_JOB_DEFAULT_LIMIT,
    ADMIN_JOB_LIST_LIMIT,
    ADMIN_WORKER_STALE_SECONDS,
)
from app.admin_ops.redaction import redact_admin_meta
from contracts.api_types import (
    AdminApiCallListResponse,
    AdminApiCallSummary,
    AdminJobListResponse,
    AdminJobSummary,
    AdminPipelineFunnelResponse,
    AdminWorkerHeartbeat,
    AdminWorkerStatusResponse,
)

logger = logging.getLogger(__name__)


class AdminOpsStore(Protocol):
    """Minimal async store protocol used by ``AdminOpsService``."""

    async def list_api_calls(
        self, *, limit: int, operation: str | None = None
    ) -> list[dict]:
        """Return newest api_calls rows."""

    async def list_worker_heartbeats(
        self, *, stale_after_seconds: float = ADMIN_WORKER_STALE_SECONDS
    ) -> list[dict]:
        """Return worker heartbeat rows with healthy flags."""

    async def list_jobs(
        self, *, limit: int, status: str | None = None
    ) -> tuple[list[dict], dict[str, int]]:
        """Return job rows and status counts."""

    async def pipeline_funnel(self) -> dict:
        """Return aggregate funnel counts."""


def _clamp_limit(raw: int | None, *, default: int, maximum: int) -> int:
    """Clamp a client-supplied limit into a safe positive range.

    Args:
        raw: Optional client limit.
        default: Value used when ``raw`` is absent.
        maximum: Inclusive upper bound.

    Returns:
        An integer in ``[1, maximum]``.
    """
    logger.info(
        "_clamp_limit called raw=%s default=%s maximum=%s",
        raw,
        default,
        maximum,
    )
    if raw is None:
        return default
    return max(1, min(int(raw), maximum))


class AdminOpsService:
    """Compose redacted observability payloads for the admin UI."""

    def __init__(self, store: AdminOpsStore) -> None:
        """Bind the injectable read store.

        Args:
            store: Repository or fake implementing ``AdminOpsStore``.
        """
        self._store = store
        logger.info("AdminOpsService initialized")

    async def list_api_calls(
        self,
        *,
        limit: int | None = None,
        operation: str | None = None,
    ) -> AdminApiCallListResponse:
        """List recent GenAI calls with prompt-safe metadata.

        Args:
            limit: Optional client page size.
            operation: Optional exact operation filter.

        Returns:
            Frozen ``AdminApiCallListResponse`` with redacted metas.
        """
        bounded = _clamp_limit(
            limit,
            default=ADMIN_API_CALL_DEFAULT_LIMIT,
            maximum=ADMIN_API_CALL_LIST_LIMIT,
        )
        op = operation.strip() if operation else None
        logger.info(
            "AdminOpsService.list_api_calls called limit=%s operation=%s",
            bounded,
            op,
        )
        rows = await self._store.list_api_calls(limit=bounded, operation=op)
        calls = [
            AdminApiCallSummary(
                id=row["id"],
                model=row["model"],
                operation=row["operation"],
                status=row["status"],
                latency_ms=row.get("latency_ms"),
                estimated_cost_microusd=row.get("estimated_cost_microusd"),
                created_at=row["created_at"],
                request_meta=redact_admin_meta(row.get("request_meta") or {}),
                response_meta=redact_admin_meta(row.get("response_meta") or {}),
            )
            for row in rows
        ]
        logger.info("AdminOpsService.list_api_calls completed count=%s", len(calls))
        return AdminApiCallListResponse(calls=calls)

    async def worker_status(self) -> AdminWorkerStatusResponse:
        """Return worker heartbeat strip state.

        Returns:
            Frozen worker status response including ``any_healthy``.
        """
        logger.info("AdminOpsService.worker_status called")
        rows = await self._store.list_worker_heartbeats(
            stale_after_seconds=ADMIN_WORKER_STALE_SECONDS
        )
        workers = [
            AdminWorkerHeartbeat(
                worker_id=row["worker_id"],
                process_id=row.get("process_id"),
                status=row.get("status"),
                started_at=row.get("started_at"),
                heartbeat_at=row.get("heartbeat_at"),
                healthy=bool(row.get("healthy")),
                metadata=redact_admin_meta(row.get("metadata") or {}),
            )
            for row in rows
        ]
        response = AdminWorkerStatusResponse(
            workers=workers,
            any_healthy=any(worker.healthy for worker in workers),
        )
        logger.info(
            "AdminOpsService.worker_status completed worker_count=%s any_healthy=%s",
            len(workers),
            response.any_healthy,
        )
        return response

    async def list_jobs(
        self,
        *,
        limit: int | None = None,
        status: str | None = None,
    ) -> AdminJobListResponse:
        """List recent gauntlet jobs and status histogram.

        Args:
            limit: Optional client page size.
            status: Optional exact status filter for detail rows.

        Returns:
            Frozen job list with aggregate counts.
        """
        bounded = _clamp_limit(
            limit,
            default=ADMIN_JOB_DEFAULT_LIMIT,
            maximum=ADMIN_JOB_LIST_LIMIT,
        )
        status_filter = status.strip() if status else None
        logger.info(
            "AdminOpsService.list_jobs called limit=%s status=%s",
            bounded,
            status_filter,
        )
        rows, counts = await self._store.list_jobs(limit=bounded, status=status_filter)
        jobs = [
            AdminJobSummary(
                id=row["id"],
                kind=row["kind"],
                turn_id=row.get("turn_id"),
                status=row["status"],
                tries=int(row.get("tries") or 0),
                last_error=row.get("last_error"),
                created_at=row["created_at"],
                available_at=row.get("available_at"),
                claimed_at=row.get("claimed_at"),
                completed_at=row.get("completed_at"),
            )
            for row in rows
        ]
        logger.info("AdminOpsService.list_jobs completed count=%s", len(jobs))
        return AdminJobListResponse(jobs=jobs, counts_by_status=counts)

    async def pipeline_funnel(self) -> AdminPipelineFunnelResponse:
        """Return aggregate eligibility funnel counts for the metrics panel.

        Returns:
            Frozen funnel response.
        """
        logger.info("AdminOpsService.pipeline_funnel called")
        raw = await self._store.pipeline_funnel()
        response = AdminPipelineFunnelResponse(**raw)
        logger.info(
            "AdminOpsService.pipeline_funnel completed validated=%s eligible=%s",
            response.validated_pairs,
            response.training_eligible_pairs,
        )
        return response
