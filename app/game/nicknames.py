"""Case-insensitive nickname reservation helpers for player join.

Guarantees every persisted display name is unique under ``lower(nickname)``
while preserving the caller's requested friendly name when available. On
collision, candidates append a compact readable ``#N`` suffix and truncate the
base so the result always fits the 32-character schema/API limit.

Architectural boundary:
- Pure allocation logic shared by Postgres and in-memory stores.
- Does not touch the database; callers perform the insert and retry on
  uniqueness violations. Stores must not rely on read-then-write races.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Must stay aligned with ``contracts/schema.sql`` and ``JoinRequest`` max_length.
NICKNAME_MAX_LEN = 32


def allocate_nickname_candidate(requested: str, attempt: int) -> str:
    """Build a nickname candidate for the given allocation attempt.

    Args:
        requested: Caller-requested display name (already stripped; 1–32 chars).
        attempt: Zero-based attempt index. ``0`` preserves ``requested`` exactly
            (still capped to ``NICKNAME_MAX_LEN``). Later attempts append a
            compact ``#N`` suffix where ``N = attempt + 1``.

    Returns:
        A nickname string of length 1..``NICKNAME_MAX_LEN``.

    Side effects:
        Logs the call at INFO with safe metadata (lengths and attempt only).
    """
    logger.info(
        "allocate_nickname_candidate called requested_len=%s attempt=%s",
        len(requested),
        attempt,
    )
    base = requested.strip()
    if not base:
        base = "player"
    base = base[:NICKNAME_MAX_LEN]
    if attempt <= 0:
        return base

    suffix = f"#{attempt + 1}"
    max_base_len = NICKNAME_MAX_LEN - len(suffix)
    if max_base_len < 1:
        # Extremely defensive: suffix alone must still fit.
        return suffix[-NICKNAME_MAX_LEN:]
    trimmed = base[:max_base_len].rstrip()
    if not trimmed:
        trimmed = "p"
        trimmed = trimmed[:max_base_len]
    candidate = f"{trimmed}{suffix}"
    logger.info(
        "allocate_nickname_candidate produced candidate_len=%s attempt=%s",
        len(candidate),
        attempt,
    )
    return candidate


def next_free_nickname(
    requested: str,
    *,
    is_taken: Callable[[str], bool],
    max_attempts: int,
) -> str:
    """Reserve the first free nickname candidate under a caller-provided probe.

    Args:
        requested: Preferred display name.
        is_taken: Callback ``(candidate: str) -> bool`` that returns True when
            ``candidate`` collides case-insensitively with an existing name.
        max_attempts: Upper bound on candidate probes.

    Returns:
        An available nickname within ``NICKNAME_MAX_LEN``.

    Raises:
        RuntimeError: When every attempt collides (should be unreachable in
            normal demo load).

    Side effects:
        Logs the call and the successful reservation metadata at INFO.
    """
    logger.info(
        "next_free_nickname called requested_len=%s max_attempts=%s",
        len(requested),
        max_attempts,
    )
    for attempt in range(max_attempts):
        candidate = allocate_nickname_candidate(requested, attempt)
        if not is_taken(candidate):
            logger.info(
                "next_free_nickname reserved candidate_len=%s attempt=%s "
                "exact=%s",
                len(candidate),
                attempt,
                attempt == 0,
            )
            return candidate
    logger.info(
        "next_free_nickname exhausted requested_len=%s max_attempts=%s",
        len(requested),
        max_attempts,
    )
    raise RuntimeError("unable to allocate a unique nickname")
