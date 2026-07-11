"""Central configuration constants for the demo-safe deck administration API.

These values bound list responses, persisted failure details, and the
transaction-wide advisory lock used to serialize deck activation. Runtime
secrets and paths remain in ``app.config.Settings``.
"""

DECK_LIST_LIMIT = 100
FAILURE_REASON_MAX_CHARS = 500
ACTIVATION_ADVISORY_LOCK_ID = 1_146_323_075
MEDIA_URL_PREFIX = "/media"
