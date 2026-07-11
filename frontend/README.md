# Frontend handoff

Arindam owns this React/Vite app. Its source of truth is the frozen backend
contract in [`../contracts/api_types.py`](../contracts/api_types.py) and the
frontend work order in
[`../build-docs/prompts-and-frontend-handoff.md`](../build-docs/prompts-and-frontend-handoff.md).

The Docker image / FastAPI process serves the Vite production build:

- `/` — player game (join → queue → rounds)
- `/tv` — venue leaderboard + metrics ticker
- `/admin` — operator decks / metrics / redacted traces (admin key in
  sessionStorage)

Keep all gameplay rules server-side; the UI renders `/api/state` and sends
actions only. Join and Queued show dual-script labels for the player's language
selections. Matchmaking heartbeats run via `POST /api/pair/request` while the
phase is `onboarding` or `queued`.
