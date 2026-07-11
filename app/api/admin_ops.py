"""Protected FastAPI routes for admin observability (metrics traces and jobs).

All routes require the same ``X-Deck-Admin-Key`` as deck administration. They
are read-only and never return raw prompts, audio, or credentials.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.admin_ops.repository import AdminOpsRepository
from app.admin_ops.service import AdminOpsService
from app.deck_admin.deps import require_deck_admin_key
from contracts.api_types import (
    AdminApiCallListResponse,
    AdminJobListResponse,
    AdminPipelineFunnelResponse,
    AdminWorkerStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin-ops"],
    dependencies=[Depends(require_deck_admin_key)],
)


def get_admin_ops_service(request: Request) -> AdminOpsService:
    """Resolve or create the process admin-ops service.

    Args:
        request: Incoming request exposing ``app.state.pool``.

    Returns:
        Shared injectable ``AdminOpsService``.

    Side effects:
        Caches the service on ``app.state`` on first use.
    """
    logger.info("get_admin_ops_service called")
    existing = getattr(request.app.state, "admin_ops_service", None)
    if existing is not None:
        return existing
    service = AdminOpsService(AdminOpsRepository(request.app.state.pool))
    request.app.state.admin_ops_service = service
    logger.info("get_admin_ops_service completed created=True")
    return service


AdminOpsDependency = Annotated[AdminOpsService, Depends(get_admin_ops_service)]


@router.get("/api-calls", response_model=AdminApiCallListResponse)
async def list_api_calls(
    service: AdminOpsDependency,
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
    operation: Annotated[str | None, Query(max_length=80)] = None,
) -> AdminApiCallListResponse:
    """Return recent redacted GenAI instrumentation rows.

    Args:
        service: Injected admin-ops service.
        limit: Optional page size (1–50).
        operation: Optional exact operation filter.

    Returns:
        Redacted call list for the Traces panel.
    """
    logger.info(
        "list_api_calls route called limit=%s operation_present=%s",
        limit,
        operation is not None,
    )
    response = await service.list_api_calls(limit=limit, operation=operation)
    logger.info("list_api_calls route completed count=%s", len(response.calls))
    return response


@router.get("/worker", response_model=AdminWorkerStatusResponse)
async def worker_status(service: AdminOpsDependency) -> AdminWorkerStatusResponse:
    """Return worker heartbeat health for the operator strip.

    Args:
        service: Injected admin-ops service.

    Returns:
        Worker liveness payload.
    """
    logger.info("worker_status route called")
    response = await service.worker_status()
    logger.info(
        "worker_status route completed workers=%s any_healthy=%s",
        len(response.workers),
        response.any_healthy,
    )
    return response


@router.get("/jobs", response_model=AdminJobListResponse)
async def list_jobs(
    service: AdminOpsDependency,
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
    status: Annotated[str | None, Query(max_length=32)] = None,
) -> AdminJobListResponse:
    """Return recent gauntlet jobs and status counts.

    Args:
        service: Injected admin-ops service.
        limit: Optional page size (1–50).
        status: Optional exact status filter.

    Returns:
        Job detail list plus aggregate histogram.
    """
    logger.info(
        "list_jobs route called limit=%s status_present=%s",
        limit,
        status is not None,
    )
    response = await service.list_jobs(limit=limit, status=status)
    logger.info("list_jobs route completed count=%s", len(response.jobs))
    return response


@router.get("/pipeline/funnel", response_model=AdminPipelineFunnelResponse)
async def pipeline_funnel(
    service: AdminOpsDependency,
) -> AdminPipelineFunnelResponse:
    """Return aggregate eligibility funnel counts for the Metrics panel.

    Args:
        service: Injected admin-ops service.

    Returns:
        Funnel aggregates without per-utterance detail.
    """
    logger.info("pipeline_funnel route called")
    response = await service.pipeline_funnel()
    logger.info(
        "pipeline_funnel route completed validated=%s eligible=%s",
        response.validated_pairs,
        response.training_eligible_pairs,
    )
    return response
