# Wave 1 — Parallel Build

## Scope

Runs architecture Phases 1–5 concurrently after Wave 0 freezes contracts.

## Parallel workstreams and write ownership

| Workstream | Type | Exclusive paths |
|---|---|---|
| Game core | Coding | `app/api/`, `app/game/`, `tests/game/` |
| Cleaning gauntlet | Coding | `worker/`, `tests/gauntlet/` |
| Deck engine | Coding | `deckgen/`, `tests/deckgen/` |
| LoRA harness | Coding | `tune/`, `tests/tune/` |
| Frontend liaison | Exploration | read-only contract support for Arindam |

Shared `app/config.py`, `app/models.py`, and GenAI infrastructure are owned by
the orchestrator. Subagents request changes instead of editing them.

## Dependency graph

```text
Wave 0 contracts
  ├── game core ───────────────┐
  ├── gauntlet ────────────────┤
  ├── deck engine ─────────────┼── orchestrator integration
  ├── LoRA harness ────────────┤
  └── frontend liaison/Arindam ┘

game smoke green ──► frontend switches from mock to real API
deck live ─────────► game replaces seeded cards
gauntlet shard ────► LoRA reads real contract-shaped data
```

## Acceptance gate

- Two simulated players complete three rounds against a seeded deck.
- Server never exposes the label before accepted audio.
- A gauntlet fixture produces eligible, silent-rejected, and contaminated
  outcomes without touching live data.
- Deck CLI dry-run works; live mode can publish a verified deck with metrics.
- Tuning dummy pipeline runs end-to-end or reports the pre-agreed fallback.
- Arindam confirms frontend fixtures match the backend state contract.
- Every workstream's focused checks pass.

## Handoff checklist

- [ ] Record component commands and process ownership.
- [ ] Record all deferred cuts and fallback modes.
- [ ] Confirm no subagent edited another owner's paths.
- [ ] Tag the contract revision used by every workstream.
- [ ] Mark decision: `PASS` / `PASS WITH CUTS` / `BLOCKED`.
