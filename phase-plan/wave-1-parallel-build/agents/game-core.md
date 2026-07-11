# Game Core — Coding Agent

## Goal

Implement join, transactional matchmaking, server-owned turn progression,
state composition, audio acceptance, guessing, scoring, and leaderboard
behavior with no Gemini dependency.

## Owned paths

`app/api/`, `app/game/`, and `tests/game/`. Contracts and shared
configuration/model files are read-only.

## Required behavior

- Match shared common language, different native language, never self-pair.
- Enforce `awaiting_audio → awaiting_label_confirm → awaiting_guess → scored`.
- Never return a label before accepted audio.
- Save server-named uploads under the runtime directory and idempotently queue
  triage with the frozen `{"turn_id": "<uuid>"}` payload.
- Perform only fast duration/silence checks inline.
- Allow two guesses; award points only on validation.
- After scoring, idempotently enqueue `package` if machine quality is already
  present; otherwise triage will enqueue it when quality becomes available.
- Compose `/api/state` in one database round trip suitable for 2-second polling.
- Enforce session caps from centralized configuration.

## Constraints

Follow `AGENTS.md`; log calls safely; add detailed module and function
documentation. Do not modify worker, deck, tune, frontend, contracts, or shared
orchestrator files. Report contract blockers instead of bypassing them.

## Verification and handoff

Create `tests/game/` coverage simulating two players completing three full
rounds against five seeded cards. Include an assertion that labels cannot leak.
Run focused tests and lint, then report files, commands, results, and risks.
