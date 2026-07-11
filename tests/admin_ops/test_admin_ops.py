"""Isolated tests for admin observability redaction and service shaping.

Uses fakes only — no Postgres, Gemini, or runtime data mutation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.admin_ops.redaction import redact_admin_meta
from app.admin_ops.service import AdminOpsService
from app.api import admin_ops


def test_router_exposes_ops_paths() -> None:
    """Register api-calls, worker, jobs, and funnel under /api/admin."""
    routes = {
        (route.path, method)
        for route in admin_ops.router.routes
        for method in (route.methods or set())
    }
    assert ("/api/admin/api-calls", "GET") in routes
    assert ("/api/admin/worker", "GET") in routes
    assert ("/api/admin/jobs", "GET") in routes
    assert ("/api/admin/pipeline/funnel", "GET") in routes


def test_redact_admin_meta_strips_prompt_text() -> None:
    """Never return triage prompt contents to the browser."""
    redacted = redact_admin_meta(
        {
            "operation": "gauntlet_triage",
            "prompt": "Player described पानी का घड़ा aloud",
            "api_key": "super-secret",
            "response": {"confidence": 0.9, "error_type": None},
        }
    )
    assert redacted["prompt"]["redacted"] is True
    assert redacted["prompt"]["char_length"] == len("Player described पानी का घड़ा aloud")
    assert "पानी" not in str(redacted["prompt"])
    assert redacted["api_key"]["redacted"] is True
    assert redacted["operation"] == "gauntlet_triage"
    assert redacted["response"]["confidence"] == 0.9


class _FakeStore:
    """In-memory AdminOpsStore for service tests."""

    async def list_api_calls(self, *, limit: int, operation: str | None = None):
        call_id = uuid4()
        return [
            {
                "id": call_id,
                "model": "gemini-3.5-flash",
                "operation": operation or "gauntlet_triage",
                "status": "success",
                "latency_ms": 120,
                "estimated_cost_microusd": 1500,
                "created_at": datetime(2026, 7, 11, tzinfo=UTC),
                "request_meta": {"prompt": "secret label text", "operation": "gauntlet_triage"},
                "response_meta": {"input_token_count": 40, "output_token_count": 12},
            }
        ][:limit]

    async def list_worker_heartbeats(self, *, stale_after_seconds: float = 45.0):
        return [
            {
                "worker_id": "worker-1",
                "process_id": 42,
                "status": "running",
                "started_at": datetime(2026, 7, 11, tzinfo=UTC),
                "heartbeat_at": datetime(2026, 7, 11, tzinfo=UTC),
                "healthy": True,
                "metadata": {"fake_gemini": True},
            }
        ]

    async def list_jobs(self, *, limit: int, status: str | None = None):
        job = {
            "id": uuid4(),
            "kind": "triage",
            "turn_id": uuid4(),
            "status": status or "failed",
            "tries": 3,
            "last_error": "RuntimeError: boom",
            "created_at": datetime(2026, 7, 11, tzinfo=UTC),
            "available_at": datetime(2026, 7, 11, tzinfo=UTC),
            "claimed_at": None,
            "completed_at": None,
        }
        return [job][:limit], {"failed": 1, "pending": 2}

    async def pipeline_funnel(self):
        return {
            "validated_pairs": 10,
            "packaged_records": 8,
            "training_eligible_pairs": 6,
            "gauntlet_pass_rate": 0.75,
            "jobs_pending": 2,
            "jobs_processing": 0,
            "jobs_failed": 1,
        }


@pytest.mark.asyncio
async def test_service_list_api_calls_redacts_prompt() -> None:
    """Service responses must not include raw prompt strings."""
    service = AdminOpsService(_FakeStore())
    response = await service.list_api_calls(limit=10, operation="gauntlet_triage")
    assert len(response.calls) == 1
    assert response.calls[0].request_meta["prompt"]["redacted"] is True
    assert "secret label text" not in str(response.calls[0].request_meta)


@pytest.mark.asyncio
async def test_service_worker_and_funnel_shapes() -> None:
    """Worker and funnel payloads match frozen contract fields."""
    service = AdminOpsService(_FakeStore())
    workers = await service.worker_status()
    funnel = await service.pipeline_funnel()
    jobs = await service.list_jobs(limit=5, status="failed")
    assert workers.any_healthy is True
    assert workers.workers[0].worker_id == "worker-1"
    assert funnel.training_eligible_pairs == 6
    assert jobs.counts_by_status["pending"] == 2
    assert jobs.jobs[0].last_error == "RuntimeError: boom"
