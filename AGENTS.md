# Dialect Data Factory — Agent Guide

## Project goal

Build a QR-joinable, mobile-first charades game that turns validated dialect
speech into an append-only training corpus. The primary hackathon submission is
Track 3: Nano Banana 2 Lite produces fresh, culturally grounded picture decks.
Gemma fine-tuning is optional, cut-first scope.

Read these before changing a component:

1. `build-docs/hackathon-project-brief.md`
2. `build-docs/dialect-factory-arch.md`
3. `build-docs/prompts-and-frontend-handoff.md`

## Non-negotiable contracts

- `contracts/schema.sql`, `contracts/api_types.py`,
  `contracts/golden_record.md`, and `contracts/dirs.md` are the integration
  boundary. Do not modify them without coordinating affected owners.
- The backend owns all game rules. The frontend only renders `/api/state` and
  sends actions.
- Never reveal a card label before the speaker's audio upload succeeds.
- A record is training-eligible only after all quality gates, human validation,
  de-duplication, and contamination checks pass.
- Keep Gemini model identifiers in `app/models.py`; never inline them.
- Keep prompts as named constants in `deckgen/prompts.py` or
  `worker/prompts.py`, not inside pipeline logic.

## Component ownership

- `app/`, `contracts/`, `scripts/`: backend/orchestrator
- `frontend/`: Arindam; do not add frontend game logic from backend work
- `worker/`: standalone gauntlet; no FastAPI imports
- `deckgen/`: standalone picture-deck CLI
- `tune/`: isolated LoRA harness; reads local corpus only and never accesses DB

## WSL2/Linux development

- Shell: Bash. Keep the repository on the native WSL filesystem, not under
  `/mnt/c`, to avoid cross-filesystem performance and permission issues.
- Use `uv` with Python 3.12 for the main application environment:
  ```bash
  uv sync --python 3.12 --all-extras
  source .venv/bin/activate
  ```
- Run local services with `docker compose up -d`; inspect with
  `docker compose ps`.
- Start the backend with:
  ```bash
  uv run uvicorn app.main:app --reload
  ```
- Ollama is an optional local-development dependency and may not be installed
  inside WSL. Confirm availability and exact tags with `ollama list` before
  invoking it. If present, use `gemma4:latest` and
  `nomic-embed-text:latest` for
  local development, fixtures, or an explicitly designed offline fallback;
  do not silently substitute them for the canonical hackathon models in
  `app/models.py` or for required Gemini/Nano Banana evaluation paths.
- Runtime assets belong under `data/` and must not be committed.

## Hackathon coding and documentation rules

- Create one file per feature or tightly related feature set. Split unrelated
  behavior into separate modules.
- Add a docstring to every function explaining its behavior, inputs, outputs,
  and relevant side effects.
- Start every source file with a detailed module-level docstring or comment
  describing the feature, major use cases, and its architectural boundary.
- Maintain root-level `Design.md` whenever features, flows, contracts, or
  architecture change.
- Log every function call at INFO with its parameters. Redact secrets,
  credentials, tokens, personal data, and inline/binary payloads; log safe
  metadata such as payload type, byte length, IDs, and hashes instead.
- Log every GenAI request at INFO with model, prompt, configuration, and safe
  input metadata, plus its output. Never log API keys or inline image/audio
  data.
- Centralize all configurable values—including model names, thresholds, paths,
  limits, timeouts, and retry counts—in configuration modules. Do not scatter
  magic values through feature code.
- Every data-mutating script must provide a dry-run or isolated test mode that
  exercises behavior without changing production/runtime data.

## Delivery standards

- Prefer small, focused changes that preserve the phase boundaries.
- Add or update a smoke test for each completed component work order.
- Run the relevant test/lint command before handing work off.
- Keep the app working without Gemini calls where a component's smoke test
  requires it; external AI work must be asynchronous and retryable.
