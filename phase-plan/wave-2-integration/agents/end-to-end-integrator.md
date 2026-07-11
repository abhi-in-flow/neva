# End-to-End Integrator — Coding Agent

## Goal

Prove the full data path using isolated integration data and identify the
smallest seam corrections required.

## Owned paths

`tests/integration/` and integration-only fixtures. Component source paths and
contracts are read-only; propose seam fixes to the orchestrator.

## Scenario

Create players, match them, use a published card, upload fixture audio, confirm
the label, validate a guess, process the queued job with fake GenAI, and assert
the resulting golden record, shard line, score, leaderboard, and metrics.

## Constraints

Follow `AGENTS.md`. Use an isolated database/schema and temporary data
directory. Never invoke paid GenAI or alter live/runtime data. Avoid frontend
edits; coordinate real-device confirmation through the orchestrator and
Arindam.

## Verification and handoff

Run the complete scenario plus cleanup. Return a boundary-by-boundary result,
contract violations, proposed owner for each fix, commands, timing, and whether
the end-to-end acceptance criterion passes.
