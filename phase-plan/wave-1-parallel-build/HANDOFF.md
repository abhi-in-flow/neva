# Wave 1 Handoff

## Decision

`PASS WITH CUTS` — backend components and contract boundaries are ready for
Wave 2 integration.

## Accepted components

- Game API is wired into FastAPI. The memory-backed smoke completes three
  rounds with two players, validated-only scoring, idempotent jobs, and strict
  pre-audio label protection.
- Poll `state_version` includes rank and leaderboard changes; labels are
  returned only during `speaking_confirm_label`.
- Shared Gemini infrastructure is connected to the gauntlet and deck adapters.
  Audio uses inline FLAC structured output; Nano Banana uses native Gemini
  IMAGE response modality rather than the Imagen endpoint.
- Gauntlet dry-run, retries, triage/package dispatch, eligibility, sharding, and
  fake-client tests pass.
- Deck dry-run, verification/retry, translation, decoys, atomic publication,
  and metrics tests pass.
- Wave 1 deck-control extension accepts operator concept sets through a
  protected admin API and CLI, generates into a reviewable `ready` state, and
  atomically activates exactly one live deck.
- LoRA dummy preparation produces a deterministic 80/20 split and validates
  training/comparison dry-run configuration.
- Frontend liaison verdict is `READY TO SWITCH FROM MOCK`.

## Verification

- `uv run pytest -q` — 57 tests passed after final contract corrections.
- Deck-control extension verification — 87 repository tests passed; repository
  Ruff checks passed; migration `0003_operator_deck_control.sql` applied.
- Focused game suite — 11 tests passed.
- Turn-sequencing regression — passed 20 consecutive runs after active-turn
  ordering was made deterministic.
- Repository Python lint passed.
- Worker, deck, and tuning command-line dry-runs passed.
- Integrated API health, leaderboard, and metrics endpoints returned valid
  responses against Postgres.

## Accepted cuts and go/no-go work

- Arindam's frontend is external to this repository; integration uses the
  frozen contract and Checkpoint B handoff.
- Confirm-label re-record is temporarily hidden because no reset endpoint
  exists.
- Live Gemini deck generation and real gauntlet audio have not been invoked;
  Wave 2 performs controlled end-to-end calls.
- Real LoRA training is deferred pending a compatible WSL2/Linux ML stack and
  confirmation of Gemma 4 audio SFT support.
- Cloudflare public hostname routing remains deferred from Wave 0.

## Wave 2 entry conditions

Use isolated database/runtime data first. Seed or generate one live deck, run a
real two-phone round, process one recording through the gauntlet, reconcile the
record/shard/metrics, and only then begin load and recovery tests.
