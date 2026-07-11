"""Centralized limits for demo-grade admin observability reads.

All list caps, heartbeat freshness windows, and redaction budgets live here so
feature code does not scatter magic numbers.
"""

from __future__ import annotations

# Maximum rows returned by call/job list endpoints.
ADMIN_API_CALL_LIST_LIMIT = 50
ADMIN_JOB_LIST_LIMIT = 50

# Default list window when the client omits ``limit``.
ADMIN_API_CALL_DEFAULT_LIMIT = 25
ADMIN_JOB_DEFAULT_LIMIT = 20

# Heartbeat age beyond which a worker is treated as unhealthy on the admin strip.
ADMIN_WORKER_STALE_SECONDS = 45.0

# Prompt / long-string redaction for browser-facing traces.
ADMIN_PROMPT_PREVIEW_CHARS = 0  # length-only; never return prompt text
ADMIN_META_STRING_MAX_CHARS = 120
