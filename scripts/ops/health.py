"""Compose health-output validation for Postgres, API, and worker services.

Status and restart waits consume ``docker compose ps --format json`` output.
The worker's Compose healthcheck must execute its heartbeat command, making
health a stronger signal than process presence.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ComposeHealthError(ValueError):
    """Raised when a required Compose service is absent or unhealthy."""


def _decode_compose_rows(raw: str) -> list[dict[str, Any]]:
    """Decode array, object, or newline-delimited Compose JSON output.

    Args:
        raw: Standard output from ``docker compose ps --format json``.

    Returns:
        List of service status dictionaries.

    Raises:
        ComposeHealthError: If output is empty or malformed.

    Side effects:
        None.
    """
    stripped = raw.strip()
    if not stripped:
        raise ComposeHealthError("compose service is absent")
    try:
        decoded = json.loads(stripped)
        if isinstance(decoded, list):
            return [row for row in decoded if isinstance(row, dict)]
        if isinstance(decoded, dict):
            return [decoded]
    except json.JSONDecodeError:
        pass
    rows: list[dict[str, Any]] = []
    try:
        for line in stripped.splitlines():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ComposeHealthError("compose health row is not an object")
            rows.append(row)
    except json.JSONDecodeError as exc:
        raise ComposeHealthError("compose health output is invalid JSON") from exc
    if not rows:
        raise ComposeHealthError("compose service is absent")
    return rows


def require_service_healthy(raw: str, service: str) -> dict[str, str]:
    """Require a Compose service to be running and report healthy.

    Args:
        raw: Machine-readable Compose status output.
        service: Required service name.

    Returns:
        Metadata-only status summary.

    Raises:
        ComposeHealthError: If the service is absent, stopped, lacks a
            healthcheck, or is not healthy.

    Side effects:
        Logs only service/state/health metadata.
    """
    rows = _decode_compose_rows(raw)
    matches = [
        row
        for row in rows
        if row.get("Service") == service or row.get("Name") == service
    ]
    if not matches and len(rows) == 1:
        matches = rows
    if len(matches) != 1:
        raise ComposeHealthError(f"compose service {service} is absent or ambiguous")
    row = matches[0]
    state = str(row.get("State", "")).lower()
    health = str(row.get("Health", "")).lower()
    if state != "running":
        raise ComposeHealthError(f"compose service {service} is not running")
    if health != "healthy":
        raise ComposeHealthError(f"compose service {service} is not healthy")
    logger.info(
        "require_service_healthy called service=%s state=%s health=%s",
        service,
        state,
        health,
    )
    return {"service": service, "state": state, "health": health}
