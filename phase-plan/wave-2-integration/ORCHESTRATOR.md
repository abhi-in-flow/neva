# Wave 2 Orchestrator Runbook

## Mission

Integrate accepted components, measure realistic behavior, and establish
recovery without destabilizing individually working features.

## Startup

1. Verify Wave 1 decision and enumerate all accepted cuts.
2. Snapshot the development database and runtime-data counts.
3. Create an isolated integration namespace/database and test data directory.
4. Launch all four subagents concurrently using `agents/`.
5. Reserve cross-component seam fixes for the orchestrator.

## Integration policy

- Reproduce every issue in isolated data before editing.
- Assign a defect to the component that violates the frozen contract.
- Prefer adapters at clear boundaries over duplicated business logic.
- Never run load, destructive recovery, or fixture generation against live
  pilot data.
- Keep Gemini degradation asynchronous; gameplay must survive API backlog.

## Checkpoints

- **E2E:** one validated record reaches a shard with correct metrics.
- **Phone:** two real devices complete a round through the public URL.
- **Load:** polling target passes at the configured worker/pool count.
- **Recovery:** API, DB, and worker restart without losing accepted data.
- **Quality:** corpus counts reconcile with records and eligibility rules.

## Completion

Apply only essential seam fixes, rerun affected component checks, update
`Design.md` with measured limits and recovery behavior, fill the handoff
checklist, and publish a consolidated go/no-go report.
