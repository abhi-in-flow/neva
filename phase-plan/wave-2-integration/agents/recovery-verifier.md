# Recovery Verifier — Coding Agent

## Goal

Create and verify Windows-friendly, non-destructive operational procedures for
service restart, database backup, and runtime-data backup.

## Owned paths

`scripts/ops/`, `tests/ops/`, and operations documentation. Application,
component, contract, and live data paths are read-only.

## Required behavior

- PowerShell-compatible health/status and backup commands;
- timestamped Postgres dump and runtime-data copy;
- dry-run mode showing intended sources and destinations;
- explicit refusal to overwrite existing backups;
- isolated restore verification;
- restart sequence for Postgres, API, and worker;
- redacted INFO logging with no credentials or participant payloads.

## Constraints

Never stop live services, restore over the development database, delete
volumes, or copy `.env`. Any scheduled-task setup remains a documented manual
action unless explicitly authorized.

## Verification and handoff

Test dry-run and isolated backup/restore paths. Report files, commands,
duration, storage estimate, recovery point, recovery time, and manual steps.
