# Frontend handoff

Arindam owns this React/Vite app. Its source of truth is the frozen backend
contract in [`../contracts/api_types.py`](../contracts/api_types.py) and the
frontend work order in
[`../build-docs/prompts-and-frontend-handoff.md`](../build-docs/prompts-and-frontend-handoff.md).

The backend will serve the Vite production build at `/`. Keep all gameplay
rules server-side; the UI renders `/api/state` and sends actions only.
