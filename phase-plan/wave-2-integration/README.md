# Wave 2 — Integration and Resilience

## Scope

Runs Phase 6 after all required Wave 1 components pass independently. This wave
wires real boundaries, tests venue-scale behavior, and proves recovery paths.

## Parallel workstreams

- **End-to-end integrator** — owns integration tests and minimal seam fixes.
- **Load tester** — owns non-destructive polling/upload load tooling.
- **Data-quality auditor** — read-only corpus and metric integrity review.
- **Recovery verifier** — owns operational backup/restart scripts and docs.

Only the orchestrator assigns seam fixes in component paths; subagents do not
make competing edits to Wave 1 ownership areas.

## Dependency graph

```text
Wave 1 accepted components
  ├── end-to-end integration ──┐
  ├── isolated load tests ─────┼── orchestrator fixes seams
  ├── data-quality audit ──────┤          │
  └── recovery verification ───┘          ▼
                                  venue-ready release
```

## Acceptance gate

- Real deck → game → audio → gauntlet → shard works end-to-end.
- Arindam's frontend completes a real two-phone round.
- 200 simulated polling clients do not corrupt state or exhaust DB connections.
- API remains responsive while Gemini calls are delayed or failing.
- Metrics reconcile with records and exclude ineligible samples.
- Postgres restart preserves data; documented backup/restore dry-run succeeds.
- No load or recovery test mutates live pilot data.

## Handoff checklist

- [ ] Record measured latency, throughput, backlog, and error rates.
- [ ] Record tested recovery commands and backup location.
- [ ] Freeze the release configuration and known safe concurrency.
- [ ] List non-critical defects accepted for launch.
- [ ] Mark decision: `PASS` / `PASS WITH CUTS` / `BLOCKED`.
