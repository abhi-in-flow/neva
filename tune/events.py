"""Write safe machine-readable events and results for tuning orchestration.

The isolated tuning CLIs use this module to expose bounded progress without
sharing participant audio, unrestricted filesystem paths, or model internals.
JSONL events are append-only; terminal result documents use atomic replacement.
The module has no backend, worker, database, GPU, or model dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
MAX_EVENT_MESSAGE_LENGTH = 240
MAX_EVENT_STAGE_LENGTH = 64


def utc_now() -> str:
    """Return the current timezone-aware UTC timestamp as an ISO string."""
    LOGGER.info("utc_now called")
    return datetime.now(UTC).isoformat()


def bounded_text(value: object, limit: int) -> str:
    """Return one control-free, length-bounded line suitable for public status."""
    LOGGER.info("bounded_text called value_type=%s limit=%d", type(value).__name__, limit)
    text = " ".join(str(value).split())
    return text[:limit]


def safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Copy allowlisted scalar metadata while rejecting paths and nested payloads."""
    LOGGER.info("safe_metadata called provided=%s", metadata is not None)
    if metadata is None:
        return {}
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        safe_key = bounded_text(key, 48)
        if not safe_key or "path" in safe_key.lower():
            continue
        if isinstance(value, bool | int | float) or value is None:
            safe[safe_key] = value
        elif isinstance(value, str) and not Path(value).is_absolute():
            safe[safe_key] = bounded_text(value, MAX_EVENT_MESSAGE_LENGTH)
    return safe


class JsonlEventWriter:
    """Append safe progress events to an optional caller-selected JSONL file."""

    def __init__(self, path: Path | None) -> None:
        """Store the optional event destination without creating it."""
        LOGGER.info("JsonlEventWriter.__init__ called enabled=%s", path is not None)
        self.path = path

    def emit(
        self,
        stage: str,
        progress: float,
        message: str,
        **metadata: Any,
    ) -> dict[str, Any]:
        """Append one bounded stage event and return its serialized object."""
        LOGGER.info(
            "JsonlEventWriter.emit called stage=%s progress=%s metadata_keys=%s",
            bounded_text(stage, MAX_EVENT_STAGE_LENGTH),
            progress,
            sorted(metadata),
        )
        if not 0.0 <= progress <= 1.0:
            raise ValueError("event progress must be between 0 and 1")
        event: dict[str, Any] = {
            "timestamp": utc_now(),
            "stage": bounded_text(stage, MAX_EVENT_STAGE_LENGTH),
            "progress": round(progress, 4),
            "message": bounded_text(message, MAX_EVENT_MESSAGE_LENGTH),
        }
        event.update(safe_metadata(metadata))
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return event


def write_result(path: Path | None, payload: dict[str, Any]) -> None:
    """Atomically write an optional structured CLI result document."""
    LOGGER.info(
        "write_result called enabled=%s keys=%s",
        path is not None,
        sorted(payload),
    )
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(serialized)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)
