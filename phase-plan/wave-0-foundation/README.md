# Wave 0 — Foundation

## Scope

Completes Phase 0: freeze integration contracts and prove the Windows-hosted
FastAPI/Postgres baseline before feature work begins.

## Parallel workstreams

- **Contract auditor** — read-only review of architecture, schema, API, golden
  record, and frontend handoff.
- **Runtime verifier** — owns backend smoke tests and runtime corrections.
- **Operations explorer** — read-only tunnel, ffmpeg, and Windows readiness
  checklist.

The three start together. The orchestrator alone may edit frozen contracts
after reviewing the contract auditor's findings.

## Dependency graph

```text
existing scaffold
  ├── contract auditor ──────┐
  ├── runtime verifier ──────┼── orchestrator arbitration
  └── operations explorer ───┘            │
                                          ▼
                              phone → tunnel → API → Postgres
```

## Acceptance gate

- `uv sync --python 3.12 --all-extras` succeeds.
- Postgres is healthy and `contracts/schema.sql` applies cleanly.
- `/api/health` returns `{"status":"ok","database":"connected"}`.
- Root static page loads through the chosen HTTPS tunnel from a phone.
- Contract audit has no unresolved critical mismatch.
- Runtime directories are created and ignored by Git.
- No credential or runtime data is staged.

## Handoff checklist

- [ ] Record contract changes and notify all Wave 1 owners.
- [ ] Record exact Windows startup commands.
- [ ] Save tunnel configuration instructions without credentials.
- [ ] Confirm Wave 1 path ownership has no overlap.
- [ ] Mark decision: `PASS` / `PASS WITH CUTS` / `BLOCKED`.
