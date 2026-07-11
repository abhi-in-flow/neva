# Wave 0 Orchestrator Runbook

## Mission

Turn the scaffold into a verified, frozen foundation. Do not start game,
gauntlet, deck, tuning, or frontend implementation in this wave.

## Inputs

- `AGENTS.md` and `Design.md`
- all three `build-docs/` documents, in documented order
- `contracts/`, `app/`, `scripts/`, `docker-compose.yml`, and `pyproject.toml`

## Startup

1. Confirm `.env` exists and is ignored; never read or print secret values.
2. Start all three subagents concurrently using the briefs in `agents/`.
3. Give each agent exclusive path ownership and prohibit contract edits.
4. While agents run, inspect Git status and ensure runtime artifacts are
   excluded.

## Arbitration

Classify findings as:

- **contract defect** — orchestrator edits the contract, then notifies every
  dependent owner;
- **implementation defect** — return it to the owning coding agent;
- **environment defect** — document an exact Windows remediation;
- **scope expansion** — defer unless required by the acceptance gate.

## Integration sequence

1. Review the contract audit before accepting runtime fixes.
2. Run dependency sync and lint.
3. Start Postgres and apply the schema.
4. Start FastAPI and check health locally.
5. Verify the static root and health endpoint through HTTPS on a phone.
6. Update `Design.md` with any accepted architectural changes.

## Stop conditions

Stop and report instead of improvising when a frozen frontend-visible shape
must change, Docker data would be destroyed, a credential is missing, or the
public tunnel requires an unapproved account/DNS mutation.

## Completion

Fill the Wave 0 handoff checklist and publish one consolidated report using the
shared format in `phase-plan/README.md`.
