# Wave 1 Orchestrator Runbook

## Mission

Build five contract-driven components in parallel without cross-agent edits,
then integrate only after each component meets its own done condition.

## Startup

1. Verify Wave 0 is marked `PASS` or explicitly accept its listed cuts.
2. Record the current contract revision and assign the briefs under `agents/`.
3. Launch game, gauntlet, deck, and tuning coding agents concurrently.
4. Launch the frontend liaison as read-only; Arindam retains `frontend/`
   ownership.
5. Reserve shared config/model/GenAI files for orchestrator-only edits.

## Coordination checkpoints

- **Checkpoint A:** agents confirm inputs and write paths before coding.
- **Checkpoint B:** game smoke test green; notify Arindam to integrate.
- **Checkpoint C:** each standalone process demonstrates dry-run/test mode.
- **Checkpoint D:** orchestrator reviews handoffs and performs cross-component
  wiring without delegating overlapping changes.

## Contract change protocol

If an agent is blocked, collect the exact mismatch and affected consumers.
Pause only affected workstreams. The orchestrator makes one minimal contract
change, updates `Design.md`, and broadcasts the new revision. Never allow
agents to maintain divergent local contract interpretations.

## Integration order

1. Game core with seeded cards and no Gemini.
2. Gauntlet against fixture jobs.
3. Deck output into gameplay.
4. Real game audio through gauntlet into corpus shards.
5. Corpus fixture into tuning harness.
6. Frontend contract confirmation with Arindam.

## Scope cuts

Cut in this order: on-device inference, Live API flourishes, leaderboard
polish, model-quality theatre. Never cut the core game loop, cleaning gates,
deck pipeline, or throughput instrumentation.

## Completion

Run focused tests and repository lint, update `Design.md`, fill the Wave 1
handoff checklist, and publish one consolidated report.
