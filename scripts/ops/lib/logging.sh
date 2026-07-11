# Wave 2 recovery operations — redacted INFO logging helpers.
#
# Provides strict-mode setup and logging helpers that never emit credentials,
# `.env` contents, or participant payloads. Callers pass only safe metadata.

neva_ops_init_logging() {
  # Initialize Bash strict mode and a consistent INFO log prefix.
  #
  # Side effects:
  #   Enables errexit, nounset, and pipefail for the current shell.
  set -euo pipefail
  export NEVA_OPS_LOG_PREFIX="${NEVA_OPS_LOG_PREFIX:-INFO neva-ops}"
}

neva_ops_log() {
  # Log a single INFO line with the shared prefix.
  #
  # Args:
  #   $1: Message safe for operator logs (no secrets or payloads).
  local message="$1"
  printf '%s: %s\n' "${NEVA_OPS_LOG_PREFIX}" "${message}" >&2
}

neva_ops_log_kv() {
  # Log an INFO line with a key/value pair.
  #
  # Args:
  #   $1: Metadata key.
  #   $2: Metadata value safe for logs.
  local key="$1"
  local value="$2"
  neva_ops_log "${key}=${value}"
}

neva_ops_redact_url() {
  # Print redacted database URL metadata as host:port/database.
  #
  # Args:
  #   $1: Full DATABASE_URL that may contain credentials.
  #
  # Outputs:
  #   Redacted connection summary suitable for INFO logs.
  local database_url="$1"
  DATABASE_URL_REDACT="${database_url}" "${NEVA_OPS_PYTHON}" -c '
import os
from urllib.parse import urlparse

parsed = urlparse(os.environ["DATABASE_URL_REDACT"])
host = parsed.hostname or "localhost"
port = parsed.port or 5432
database = (parsed.path or "").lstrip("/") or "unknown"
print(f"{host}:{port}/{database}")
'
}
