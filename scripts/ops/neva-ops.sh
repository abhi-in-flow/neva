#!/usr/bin/env bash
# Wave 2 recovery operations entrypoint for WSL2/Linux Bash environments.
#
# Provides non-destructive status probes, timestamped backups with dry-run
# support, isolated restore verification, and a deterministic restart runbook.
# Mutating paths honor fail-if-exists semantics, refuse live development
# targets for restore, and never copy or log `.env` credentials.
#
# Usage:
#   scripts/ops/neva-ops.sh status [postgres|api|worker|all]
#   scripts/ops/neva-ops.sh backup [--dry-run] [--dest PATH]
#   scripts/ops/neva-ops.sh restore-verify --source PATH --data-dir PATH
#   scripts/ops/neva-ops.sh restart [--dry-run|--execute]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export NEVA_OPS_REPO_ROOT="${NEVA_OPS_REPO_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
# shellcheck source=lib/config.sh
source "${SCRIPT_DIR}/lib/config.sh"
# shellcheck source=lib/logging.sh
source "${SCRIPT_DIR}/lib/logging.sh"

neva_ops_init_logging

neva_ops_usage() {
  # Print operator usage and exit with code 2.
  cat <<'EOF'
Usage:
  neva-ops.sh status [postgres|api|worker|all]
  neva-ops.sh backup [--dry-run] [--dest PATH] [--backup-id ID]
  neva-ops.sh restore-verify --source PATH --data-dir PATH [--dry-run]
  neva-ops.sh restart [--dry-run|--execute]

Environment overrides:
  DATA_DIR, DATABASE_URL, COMPOSE_PROJECT_NAME, NEVA_OPS_BACKUP_ROOT,
  POSTGRES_USER, POSTGRES_DB, NEVA_OPS_ISOLATED (required for restore-verify)
EOF
}

neva_ops_require_cmd() {
  # Fail fast when a required external command is unavailable.
  #
  # Args:
  #   $1: Command name expected on PATH.
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    neva_ops_log "missing required command: ${cmd}"
    exit 1
  fi
}

neva_ops_compose_health() {
  # Require one Compose service to report running/healthy.
  #
  # Args:
  #   $1: Service name with a configured Compose healthcheck.
  local service="$1"
  local compose_json=""
  neva_ops_require_cmd docker
  if ! compose_json="$(
    cd "${NEVA_OPS_REPO_ROOT}" && \
      docker compose -f "${NEVA_OPS_COMPOSE_FILE}" -p "${NEVA_OPS_COMPOSE_PROJECT}" \
        ps --format json "${service}"
  )"; then
    neva_ops_log "status service=${service} compose_query=failed"
    return 1
  fi
  if ! (
    cd "${NEVA_OPS_REPO_ROOT}" && \
      ${NEVA_OPS_PYTHON} -m scripts.ops.cli validate-compose-health \
        --service "${service}" --json "${compose_json}" >/dev/null
  ); then
    neva_ops_log "status service=${service} health=failed"
    return 1
  fi
  neva_ops_log "status service=${service} health=healthy"
}

neva_ops_status_postgres() {
  # Report Compose Postgres health without mutating services.
  neva_ops_compose_health "${NEVA_OPS_POSTGRES_SERVICE}"
}

neva_ops_status_api() {
  # Report Compose API health using its configured healthcheck.
  neva_ops_compose_health "${NEVA_OPS_API_SERVICE}"
}

neva_ops_status_worker() {
  # Report worker heartbeat health through its Compose healthcheck.
  neva_ops_compose_health "${NEVA_OPS_WORKER_SERVICE}"
}

neva_ops_wait_healthy() {
  # Poll Compose health until a service is healthy or timeout expires.
  #
  # Args:
  #   $1: Compose service name.
  local service="$1"
  local deadline=$(( $(date +%s) + NEVA_OPS_HEALTH_WAIT_SECONDS ))
  while [[ "$(date +%s)" -lt "${deadline}" ]]; do
    if neva_ops_compose_health "${service}"; then
      return 0
    fi
    sleep "${NEVA_OPS_HEALTH_POLL_SECONDS}"
  done
  neva_ops_log "wait service=${service} timed_out_seconds=${NEVA_OPS_HEALTH_WAIT_SECONDS}"
  return 1
}

neva_ops_status_all() {
  # Aggregate Postgres, API, and worker status without stopping services.
  local postgres_rc=0
  local api_rc=0
  local worker_rc=0
  neva_ops_status_postgres || postgres_rc=$?
  neva_ops_status_api || api_rc=$?
  neva_ops_status_worker || worker_rc=$?
  neva_ops_log_kv "status_summary_postgres" "${postgres_rc}"
  neva_ops_log_kv "status_summary_api" "${api_rc}"
  neva_ops_log_kv "status_summary_worker" "${worker_rc}"
  if [[ "${postgres_rc}" -ne 0 || "${api_rc}" -ne 0 || "${worker_rc}" -ne 0 ]]; then
    return 1
  fi
  neva_ops_log "status all=ok"
}

neva_ops_backup() {
  # Create or plan a timestamped backup with DB-first dump ordering.
  local dry_run=0
  local dest=""
  local backup_id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)
        dry_run=1
        shift
        ;;
      --dest)
        dest="$2"
        shift 2
        ;;
      --backup-id)
        backup_id="$2"
        shift 2
        ;;
      *)
        neva_ops_usage
        exit 2
        ;;
    esac
  done

  if [[ -z "${dest}" ]]; then
    backup_id="${backup_id:-$(date -u +%Y%m%dT%H%M%SZ)}"
    dest="${NEVA_OPS_BACKUP_ROOT}/neva-${backup_id}"
  fi

  neva_ops_log "backup begin dry_run=${dry_run} dest=${dest}"
  neva_ops_log_kv "database_target" "$(neva_ops_redact_url "${NEVA_OPS_DATABASE_URL}")"
  neva_ops_log_kv "data_dir" "${NEVA_OPS_DATA_DIR}"

  local plan_json
  plan_json="$(
    cd "${NEVA_OPS_REPO_ROOT}" && \
      ${NEVA_OPS_PYTHON} -m scripts.ops.cli validate-backup \
        --destination "${dest}" \
        --data-dir "${NEVA_OPS_DATA_DIR}" \
        --database-url "${NEVA_OPS_DATABASE_URL}" \
        --compose-file "${NEVA_OPS_COMPOSE_FILE}" \
        --compose-project "${NEVA_OPS_COMPOSE_PROJECT}" \
        --postgres-user "${NEVA_OPS_POSTGRES_USER}" \
        --postgres-db "${NEVA_OPS_POSTGRES_DB}" \
        ${backup_id:+--backup-id "${backup_id}"} \
        $([[ "${dry_run}" -eq 1 ]] && printf '%s' '--dry-run')
  )" || {
    neva_ops_log "backup validation failed"
    printf '%s\n' "${plan_json}"
    exit 2
  }

  printf '%s\n' "${plan_json}"

  if [[ "${dry_run}" -eq 1 ]]; then
    neva_ops_log "backup dry_run complete; no mutations performed"
    return 0
  fi

  neva_ops_require_cmd docker
  neva_ops_require_cmd gzip
  neva_ops_require_cmd rsync
  mkdir -m "${NEVA_OPS_BACKUP_DIR_MODE}" -p "${dest}/postgres" "${dest}/runtime"
  local database_counts
  local database_counts_sql
  database_counts_sql="$(
    cd "${NEVA_OPS_REPO_ROOT}" && \
      ${NEVA_OPS_PYTHON} -m scripts.ops.cli emit-sql --kind database-counts
  )"
  neva_ops_log "backup database count snapshot begin"
  database_counts="$(
    cd "${NEVA_OPS_REPO_ROOT}" && \
      docker compose -f "${NEVA_OPS_COMPOSE_FILE}" -p "${NEVA_OPS_COMPOSE_PROJECT}" \
        exec -T postgres psql -X -v ON_ERROR_STOP=1 \
          -U "${NEVA_OPS_POSTGRES_USER}" -d "${NEVA_OPS_POSTGRES_DB}" -Atc \
          "${database_counts_sql}"
  )"
  neva_ops_log "backup postgres dump begin"
  (
    cd "${NEVA_OPS_REPO_ROOT}"
    docker compose -f "${NEVA_OPS_COMPOSE_FILE}" -p "${NEVA_OPS_COMPOSE_PROJECT}" \
      exec -T postgres pg_dump -U "${NEVA_OPS_POSTGRES_USER}" "${NEVA_OPS_POSTGRES_DB}" \
      | gzip > "${dest}/postgres/dump.sql.gz"
  )
  neva_ops_log "backup runtime copy begin"
  rsync -a \
    --exclude '.env*' \
    "${NEVA_OPS_DATA_DIR}/" "${dest}/runtime/"

  backup_id="${backup_id:-$(basename "${dest}")}"
  backup_id="${backup_id#neva-}"
  cd "${NEVA_OPS_REPO_ROOT}" && \
    ${NEVA_OPS_PYTHON} -m scripts.ops.cli finalize-backup \
      --destination "${dest}" \
      --data-dir "${NEVA_OPS_DATA_DIR}" \
      --database-url "${NEVA_OPS_DATABASE_URL}" \
      --backup-id "${backup_id}" \
      --database-counts-json "${database_counts}"
  neva_ops_log "backup complete dest=${dest}"
}

neva_ops_restore_verify() {
  # Restore and prove integrity only in explicitly isolated, fresh targets.
  local dry_run=0
  local source=""
  local data_dir=""
  local report=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)
        dry_run=1
        shift
        ;;
      --source)
        source="$2"
        shift 2
        ;;
      --data-dir)
        data_dir="$2"
        shift 2
        ;;
      --report)
        report="$2"
        shift 2
        ;;
      *)
        neva_ops_usage
        exit 2
        ;;
    esac
  done

  if [[ -z "${source}" || -z "${data_dir}" ]]; then
    neva_ops_usage
    exit 2
  fi
  report="${report:-${source}/restore-verification-$(date -u +%Y%m%dT%H%M%SZ).json}"

  neva_ops_log \
    "restore-verify begin source=${source} data_dir=${data_dir} report=${report} dry_run=${dry_run}"
  local validation_json
  validation_json="$(
    cd "${NEVA_OPS_REPO_ROOT}" && \
      ${NEVA_OPS_PYTHON} -m scripts.ops.cli validate-restore \
        --source "${source}" \
        --data-dir "${data_dir}" \
        --database-url "${NEVA_OPS_DATABASE_URL}" \
        --compose-file "${NEVA_OPS_COMPOSE_FILE}" \
        --compose-project "${NEVA_OPS_COMPOSE_PROJECT}" \
        --postgres-user "${NEVA_OPS_POSTGRES_USER}" \
        --postgres-db "${NEVA_OPS_POSTGRES_DB}" \
        --isolated-env "${NEVA_OPS_ISOLATED}" \
        --live-data-dir "${NEVA_OPS_LIVE_DATA_DIR}" \
        --live-database-url "${NEVA_OPS_LIVE_DATABASE_URL}"
  )" || {
    neva_ops_log "restore-verify validation failed"
    printf '%s\n' "${validation_json}"
    exit 2
  }
  printf '%s\n' "${validation_json}"

  if [[ "${dry_run}" -eq 1 ]]; then
    neva_ops_log "restore-verify dry_run complete; no restore performed"
    return 0
  fi

  neva_ops_require_cmd docker
  neva_ops_require_cmd gunzip
  neva_ops_require_cmd rsync
  local restore_started
  local running_dependents
  local user_table_count
  local empty_database_sql
  restore_started="$(date +%s)"
  running_dependents="$(
    cd "${NEVA_OPS_REPO_ROOT}" && \
      docker compose -f "${NEVA_OPS_COMPOSE_FILE}" -p "${NEVA_OPS_COMPOSE_PROJECT}" \
        ps --status running -q "${NEVA_OPS_API_SERVICE}" "${NEVA_OPS_WORKER_SERVICE}"
  )"
  if [[ -n "${running_dependents}" ]]; then
    neva_ops_log "restore-verify refused because isolated API or worker is running"
    return 2
  fi
  empty_database_sql="$(
    cd "${NEVA_OPS_REPO_ROOT}" && \
      ${NEVA_OPS_PYTHON} -m scripts.ops.cli emit-sql --kind empty-database
  )"
  neva_ops_log "restore-verify empty database preflight begin"
  user_table_count="$(
    cd "${NEVA_OPS_REPO_ROOT}" && \
      docker compose -f "${NEVA_OPS_COMPOSE_FILE}" -p "${NEVA_OPS_COMPOSE_PROJECT}" \
        exec -T postgres psql -X -v ON_ERROR_STOP=1 \
          -U "${NEVA_OPS_POSTGRES_USER}" -d "${NEVA_OPS_POSTGRES_DB}" -Atc \
          "${empty_database_sql}"
  )"
  if ! (
    cd "${NEVA_OPS_REPO_ROOT}" && \
      ${NEVA_OPS_PYTHON} -m scripts.ops.cli validate-db-empty \
        --user-table-count "${user_table_count}" >/dev/null
  ); then
    neva_ops_log "restore-verify refused nonempty target database"
    return 2
  fi

  neva_ops_log "restore-verify postgres restore begin"
  (
    cd "${NEVA_OPS_REPO_ROOT}"
    gunzip -c "${source}/postgres/dump.sql.gz" | \
      docker compose -f "${NEVA_OPS_COMPOSE_FILE}" -p "${NEVA_OPS_COMPOSE_PROJECT}" \
        exec -T postgres psql -U "${NEVA_OPS_POSTGRES_USER}" -d "${NEVA_OPS_POSTGRES_DB}"
  )
  neva_ops_log "restore-verify runtime copy begin"
  rsync -a \
    --exclude '.env*' \
    "${source}/runtime/" "${data_dir}/"
  local actual_database_counts
  local database_counts_sql
  local constraint_validation_sql
  local invalid_constraint_count
  local elapsed_seconds
  database_counts_sql="$(
    cd "${NEVA_OPS_REPO_ROOT}" && \
      ${NEVA_OPS_PYTHON} -m scripts.ops.cli emit-sql --kind database-counts
  )"
  constraint_validation_sql="$(
    cd "${NEVA_OPS_REPO_ROOT}" && \
      ${NEVA_OPS_PYTHON} -m scripts.ops.cli emit-sql --kind constraints
  )"
  neva_ops_log "restore-verify post-restore database checks begin"
  actual_database_counts="$(
    cd "${NEVA_OPS_REPO_ROOT}" && \
      docker compose -f "${NEVA_OPS_COMPOSE_FILE}" -p "${NEVA_OPS_COMPOSE_PROJECT}" \
        exec -T postgres psql -X -v ON_ERROR_STOP=1 \
          -U "${NEVA_OPS_POSTGRES_USER}" -d "${NEVA_OPS_POSTGRES_DB}" -Atc \
          "${database_counts_sql}"
  )"
  invalid_constraint_count="$(
    cd "${NEVA_OPS_REPO_ROOT}" && \
      docker compose -f "${NEVA_OPS_COMPOSE_FILE}" -p "${NEVA_OPS_COMPOSE_PROJECT}" \
        exec -T postgres psql -X -v ON_ERROR_STOP=1 \
          -U "${NEVA_OPS_POSTGRES_USER}" -d "${NEVA_OPS_POSTGRES_DB}" -Atc \
          "${constraint_validation_sql}"
  )"
  elapsed_seconds="$(( $(date +%s) - restore_started ))"
  (
    cd "${NEVA_OPS_REPO_ROOT}" && \
      ${NEVA_OPS_PYTHON} -m scripts.ops.cli verify-restore \
        --source "${source}" \
        --data-dir "${data_dir}" \
        --actual-database-counts-json "${actual_database_counts}" \
        --invalid-constraint-count "${invalid_constraint_count}" \
        --elapsed-seconds "${elapsed_seconds}" \
        --report "${report}"
  )
  neva_ops_log \
    "restore-verify complete data_dir=${data_dir} report=${report} elapsed_seconds=${elapsed_seconds}"
}

neva_ops_restart() {
  # Print or execute the deterministic restart sequence.
  local mode="dry-run"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)
        mode="dry-run"
        shift
        ;;
      --execute)
        mode="execute"
        shift
        ;;
      *)
        neva_ops_usage
        exit 2
        ;;
    esac
  done

  neva_ops_log "restart begin mode=${mode}"
  local plan_json
  plan_json="$(
    cd "${NEVA_OPS_REPO_ROOT}" && \
      ${NEVA_OPS_PYTHON} -m scripts.ops.cli restart-plan \
        --compose-file "${NEVA_OPS_COMPOSE_FILE}" \
        --compose-project "${NEVA_OPS_COMPOSE_PROJECT}"
  )"
  printf '%s\n' "${plan_json}"

  if [[ "${mode}" != "execute" ]]; then
    neva_ops_log "restart dry_run complete; live services were not stopped"
    return 0
  fi

  neva_ops_log "restart execute begin"
  (
    cd "${NEVA_OPS_REPO_ROOT}"
    docker compose -f "${NEVA_OPS_COMPOSE_FILE}" -p "${NEVA_OPS_COMPOSE_PROJECT}" \
      stop "${NEVA_OPS_WORKER_SERVICE}"
    docker compose -f "${NEVA_OPS_COMPOSE_FILE}" -p "${NEVA_OPS_COMPOSE_PROJECT}" \
      stop "${NEVA_OPS_API_SERVICE}"
    docker compose -f "${NEVA_OPS_COMPOSE_FILE}" -p "${NEVA_OPS_COMPOSE_PROJECT}" \
      restart "${NEVA_OPS_POSTGRES_SERVICE}"
  )
  neva_ops_wait_healthy "${NEVA_OPS_POSTGRES_SERVICE}"
  (
    cd "${NEVA_OPS_REPO_ROOT}"
    docker compose -f "${NEVA_OPS_COMPOSE_FILE}" -p "${NEVA_OPS_COMPOSE_PROJECT}" \
      up -d --no-deps "${NEVA_OPS_API_SERVICE}"
  )
  neva_ops_wait_healthy "${NEVA_OPS_API_SERVICE}"
  (
    cd "${NEVA_OPS_REPO_ROOT}"
    docker compose -f "${NEVA_OPS_COMPOSE_FILE}" -p "${NEVA_OPS_COMPOSE_PROJECT}" \
      up -d --no-deps "${NEVA_OPS_WORKER_SERVICE}"
  )
  neva_ops_wait_healthy "${NEVA_OPS_WORKER_SERVICE}"
  neva_ops_log "restart execute completed full_stack=healthy"
}

main() {
  # Dispatch subcommands for status, backup, restore verification, and restart.
  if [[ $# -lt 1 ]]; then
    neva_ops_usage
    exit 2
  fi

  local command="$1"
  shift

  case "${command}" in
    status)
      local target="${1:-all}"
      case "${target}" in
        postgres) neva_ops_status_postgres ;;
        api) neva_ops_status_api ;;
        worker) neva_ops_status_worker ;;
        all) neva_ops_status_all ;;
        *)
          neva_ops_usage
          exit 2
          ;;
      esac
      ;;
    backup) neva_ops_backup "$@" ;;
    restore-verify) neva_ops_restore_verify "$@" ;;
    restart) neva_ops_restart "$@" ;;
    -h|--help) neva_ops_usage ;;
    *)
      neva_ops_usage
      exit 2
      ;;
  esac
}

main "$@"
