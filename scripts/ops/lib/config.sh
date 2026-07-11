# Wave 2 recovery operations — centralized Bash configuration.
#
# All tunable paths, URLs, timeouts, and service names live here so Bash
# entrypoints and tests share one source of truth. Values may be overridden
# through environment variables without editing scripts.

# Repository and compose defaults (repo root is set by neva-ops.sh before sourcing)
NEVA_OPS_REPO_ROOT="${NEVA_OPS_REPO_ROOT:-$(pwd)}"
NEVA_OPS_COMPOSE_FILE="${NEVA_OPS_COMPOSE_FILE:-docker-compose.yml}"
NEVA_OPS_COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-neva}"

# Runtime and backup paths
NEVA_OPS_DATA_DIR="${HOST_DATA_DIR:-${DATA_DIR:-${NEVA_OPS_REPO_ROOT}/data}}"
NEVA_OPS_LIVE_DATA_DIR="${NEVA_OPS_LIVE_DATA_DIR:-${NEVA_OPS_REPO_ROOT}/data}"
NEVA_OPS_BACKUP_ROOT="${NEVA_OPS_BACKUP_ROOT:-${NEVA_OPS_REPO_ROOT}/backups}"
NEVA_OPS_BACKUP_DIR_MODE="${NEVA_OPS_BACKUP_DIR_MODE:-700}"

# Postgres connection metadata (credentials come from env, never logged)
NEVA_OPS_DATABASE_URL="${DATABASE_URL:-postgresql://dialect:dialect_dev_only@localhost:5432/dialect_factory}"
NEVA_OPS_LIVE_DATABASE_URL="${NEVA_OPS_LIVE_DATABASE_URL:-postgresql://dialect:dialect_dev_only@localhost:5432/dialect_factory}"
NEVA_OPS_POSTGRES_USER="${POSTGRES_USER:-dialect}"
NEVA_OPS_POSTGRES_DB="${POSTGRES_DB:-dialect_factory}"
NEVA_OPS_POSTGRES_PORT="${POSTGRES_PORT:-5432}"

# Health and restart targets
NEVA_OPS_HEALTH_WAIT_SECONDS="${NEVA_OPS_HEALTH_WAIT_SECONDS:-60}"
NEVA_OPS_HEALTH_POLL_SECONDS="${NEVA_OPS_HEALTH_POLL_SECONDS:-2}"
NEVA_OPS_POSTGRES_SERVICE="postgres"
NEVA_OPS_API_SERVICE="api"
NEVA_OPS_WORKER_SERVICE="worker"

# Isolation markers for restore verification
NEVA_OPS_ISOLATED="${NEVA_OPS_ISOLATED:-}"
NEVA_OPS_ISOLATED_MARKER=".neva-isolated"

# Python helper entrypoint
NEVA_OPS_PYTHON="${NEVA_OPS_PYTHON:-uv run python}"
