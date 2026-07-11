# Runtime Verifier — Coding Agent

## Goal

Prove that the Python 3.12/FastAPI/Postgres baseline starts reliably on Windows
and add focused, non-destructive smoke coverage.

## Owned paths

- `app/`
- `scripts/`
- `tests/phase0/`
- `pyproject.toml` only when a missing runtime/test dependency is essential

Treat `contracts/` as read-only. Do not touch frontend, worker, deck, or tuning
paths.

## Tasks

1. Sync with `uv` and verify Python 3.12.
2. Confirm Docker Postgres health.
3. Exercise schema application against the configured development database.
4. Add a health smoke test with an isolated/test database dependency or mocked
   pool; tests must not mutate runtime data.
5. Verify startup creates required directories and clean shutdown closes the
   pool.
6. Add INFO call logging and required module/function documentation to files
   changed during this assignment.

## Verification

Run focused tests, `uv run ruff check` on owned Python paths, and an application
import smoke test. Never print `.env` or connection credentials.

## Handoff

Report files changed, commands/results, local health result, any environment
dependency, and whether the Phase 0 runtime acceptance criteria pass.
