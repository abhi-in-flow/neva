# Wave 2 Recovery Operations

Operational backup, restore verification, and restart procedures for the
Dialect Data Factory venue laptop. All commands are WSL2/Linux Bash compatible
and live under `scripts/ops/`.

## Quick reference

```bash
# Health/status (non-mutating)
scripts/ops/neva-ops.sh status all

# Plan a backup without touching live data
scripts/ops/neva-ops.sh backup --dry-run --dest /path/outside/data/backups/neva-test

# Create a timestamped backup (destination must not exist)
scripts/ops/neva-ops.sh backup --dest /path/outside/data/backups/neva-$(date -u +%Y%m%dT%H%M%SZ)

# Verify restore into an isolated namespace only
export NEVA_OPS_ISOLATED=1
export NEVA_OPS_LIVE_DATA_DIR=/path/to/neva/data  # actual development/pilot host path
export COMPOSE_PROJECT_NAME=neva_isolated
export DATABASE_URL=postgresql://dialect:dialect_dev_only@localhost:5432/dialect_factory_isolated
export CONTAINER_DATABASE_URL=postgresql://dialect:dialect_dev_only@postgres:5432/dialect_factory_isolated
export POSTGRES_DB=dialect_factory_isolated
export HOST_DATA_DIR=/tmp/neva-isolated-data
mkdir -p "${HOST_DATA_DIR}" && touch "${HOST_DATA_DIR}/.neva-isolated"
docker compose up -d postgres  # do not start migrate/api/worker before restore
scripts/ops/neva-ops.sh restore-verify --dry-run --source /path/to/backup --data-dir "${HOST_DATA_DIR}"

# Execute only against the fresh isolated DB/data target
scripts/ops/neva-ops.sh restore-verify --source /path/to/backup --data-dir "${HOST_DATA_DIR}"

# Print restart sequence without stopping live services (default)
scripts/ops/neva-ops.sh restart --dry-run
```

## RPO / RTO inputs

These are operator inputs for Wave 3 scheduling; measured values belong in
`Design.md` after orchestrator rehearsal.

| Input | Default / assumption | Notes |
|---|---|---|
| **Backup cadence** | Every 15 minutes during pilot | Matches architecture doc laptop-death mitigation |
| **RPO target** | ≤ 15 minutes | Bounded by backup interval plus in-flight turns not yet dumped |
| **RTO target (Postgres)** | ≤ 2 minutes | `docker compose restart postgres` + health probe |
| **RTO target (full stack)** | ≤ 5 minutes | Compose restart waits for Postgres, API, and heartbeat-backed worker health |
| **Backup retention** | Current day + previous day | Store outside `DATA_DIR`; mode `0700` |
| **Storage estimate** | DB dump + runtime tree size | Use `backup --dry-run` manifest `runtime_counts` and `du -sh data/` |

## Backup contract

1. Validate destination is **outside** `DATA_DIR` and **does not exist**.
2. Create destination directories with mode **0700**.
3. Capture metadata-only core-table row counts immediately before the dump.
4. **DB-first:** `pg_dump` through Compose Postgres, gzip to `postgres/dump.sql.gz`.
5. Only after the dump finishes, copy `audio/`, `decks/`, and `corpus/` to
   `runtime/` via `rsync`.
6. Exclude `.env` and every `.env.*` variant from runtime copies.
7. Write metadata-only `manifest.json` and exact aggregate
   `checksums.sha256`; every dump/runtime file must be represented exactly once.
8. Never log credentials, `.env` contents, SQL rows, or participant payloads.

The DB dump is a transaction-consistent `pg_dump` snapshot. Runtime files are a
later boundary because their copy starts after the DB dump completes. For exact
row-count comparison during restore verification, quiesce API/worker writes for
the short interval covering count capture and `pg_dump`; otherwise an accepted
turn committed between those operations can make the pre-dump count metadata
differ from the dump snapshot, causing a safe false-negative during restore.

## Isolated restore verification

Restore verification **refuses**:

- Live development `DATA_DIR` (default `./data`)
- Live database name (`dialect_factory` without `_isolated` suffix)
- Compose project names not ending in `_isolated`
- Targets missing `NEVA_OPS_ISOLATED=1`
- Targets missing `${DATA_DIR}/.neva-isolated` marker file
- Non-empty destination directories except for the required marker (no overwrite)
- Target databases containing any user table, even when all tables are empty
- Missing/malformed manifests, incomplete or corrupt checksums, corrupt/empty
  gzip dumps, unexpected files, and secret files anywhere in the backup source

Orchestrator must provision an isolated Compose project, database, and data
directory before running mutating restore verification. The database must exist
but contain zero user tables; do not apply `contracts/schema.sql` first because
the plain SQL dump creates the schema.

Before restore, the command fully reads and validates the gzip stream and hashes
every expected dump/runtime file. After restore it:

1. Re-hashes the isolated runtime target and compares exact file sets/counts.
2. Confirms `.neva-isolated` survived the runtime copy.
3. Queries metadata-only counts for all core tables and compares the manifest.
4. Requires zero unvalidated user constraints; restored foreign keys therefore
   remain enforced by Postgres.
5. Writes a metadata-only restore verification report with all gate results and
   elapsed seconds (measured RTO). No database rows or runtime payloads appear.

## Deterministic restart sequence

1. `docker compose stop worker`
2. `docker compose stop api`
3. Restart Postgres via Compose
4. Wait until Compose reports Postgres `running` and `healthy`
5. `docker compose up -d --no-deps api`, then wait for API health
6. `docker compose up -d --no-deps worker`, then wait for worker heartbeat health

Tests and operator rehearsals must use `restart --dry-run` so live services are
never stopped unintentionally. Use `restart --execute` only with orchestrator
approval during a maintenance window.

`status postgres|api|worker|all` also reads Compose JSON health. Worker status
never uses PID/process presence: the worker healthcheck must fail when its
database heartbeat is stale, so a wedged worker is reported unhealthy.

## Exact Compose and environment assumptions

The orchestrator-owned Compose stack must provide:

- Services named exactly `postgres`, `api`, and `worker`.
- A Compose healthcheck on every service. `postgres` runs `pg_isready`; `api`
  probes `/api/health`; `worker` runs `python -m worker.health` (or
  `uv run python -m worker.health` in a uv-based image) and becomes unhealthy
  when its database heartbeat is stale.
- Docker Compose v2 support for `ps --format json`, `stop`, `restart`, and
  `up -d --no-deps`.
- `POSTGRES_USER` and `POSTGRES_DB` available to ops and consistent with the
  Postgres service. Host `DATABASE_URL` and container
  `CONTAINER_DATABASE_URL` must identify the same DB.
- Worker container and healthcheck share `WORKER_ID`, `DATABASE_URL`,
  `HEARTBEAT_INTERVAL_SECONDS` (default 10), and
  `HEARTBEAT_STALE_SECONDS` (default 45); stale must exceed interval.
- Host runtime data is selected by `HOST_DATA_DIR` (preferred by ops, matching
  Compose) and mounted as container `DATA_DIR=/app/data` for API and worker.
  When the live host path is not repository `./data`, set
  `NEVA_OPS_LIVE_DATA_DIR` explicitly before isolated restore validation.
- For restore, `COMPOSE_PROJECT_NAME`, `POSTGRES_DB`, `DATABASE_URL`, and
  `CONTAINER_DATABASE_URL` names ending in `_isolated`,
  `NEVA_OPS_ISOLATED=1`, and a marker-only target `HOST_DATA_DIR`.
- The isolated DB is created but receives no schema initialization before
  restore. Its Compose volume/project must be separate from development, and
  isolated `api`/`worker` services must be stopped during restore.

## Manual scheduling (not automated here)

Wave 2 tooling does **not** install cron or systemd units. To schedule backups
on the venue laptop:

```bash
# Example cron entry (orchestrator installs manually)
*/15 * * * * cd /path/to/neva && scripts/ops/neva-ops.sh backup --dest /secure/backups/neva-$(date -u +\%Y\%m\%dT\%H\%M\%SZ) >> /secure/logs/neva-backup.log 2>&1
```

## Orchestrator shared changes still required

The recovery verifier cannot modify shared files. The orchestrator must apply:

1. **Full Compose services** — retain canonical `postgres`, `api`, and `worker`
   service names; ops restart controls only these names. The current full-stack
   Compose draft satisfies this assumption.
2. **Healthchecks** — retain API `/api/health` and worker
   `python -m worker.health` checks with shared heartbeat env. The current
   Compose draft satisfies this assumption; process/PID checks must not replace
   the worker heartbeat.
3. **Isolated integration namespace** — create a fresh
   `dialect_factory_isolated` DB,
   `COMPOSE_PROJECT_NAME=neva_isolated`, and a temp `HOST_DATA_DIR` with
   `.neva-isolated`; do not pre-apply the schema.
4. **Environment handoff** — add `HOST_DATA_DIR`,
   `NEVA_OPS_LIVE_DATA_DIR`, `NEVA_OPS_BACKUP_ROOT`, and isolated
   `CONTAINER_DATABASE_URL` guidance to the orchestrator-owned `.env.example`.
5. **Design.md** — record measured RPO/RTO after rehearsal and backup location.
6. **README.md** — add a short "Recovery" section linking here (orchestrator).

## Files owned by recovery verifier

```text
scripts/ops/
tests/ops/
phase-plan/wave-2-integration/RECOVERY.md
```
