# Dialect Data Factory

Turn a multiplayer charades game into a zero-touch, validated dialect speech-data pipeline.

## Hackathon framing

This project is built for the Google DeepMind Bangalore Hackathon:

- **Track 3 — High-Throughput Creative Workflows:** Nano Banana 2 Lite creates fresh, culturally grounded picture decks that prevent memorization and enable regional customization.
- **Gemma 4 special prize:** validated speech/text pairs flow directly into an optional same-day local LoRA fine-tune.

The primary demo claim is pipeline throughput and unit economics—not a model-quality claim.

## Architecture

- FastAPI backend, served locally and exposed through a tunnel
- Postgres 16 in Docker
- Local disk for audio, decks, and append-only corpus shards
- Independent game, deck-generation, cleaning-worker, and fine-tuning components

The frozen integration contracts live in [`contracts/`](contracts/).

## Quick start

1. Copy `.env.example` to `.env` and set `DATABASE_URL` if needed.
2. Start Postgres:
   ```sh
   docker compose up -d
   ```
3. Create the Python 3.12 environment and install dependencies:
   ```sh
   uv sync --python 3.12 --all-extras
   ```
4. Apply the schema:
   ```sh
   uv run python -m scripts.apply_schema
   ```
   For an existing development database, apply pending forward migrations:
   ```sh
   uv run python -m scripts.apply_migrations
   ```
5. Run the API:
   ```sh
   uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

Open `http://localhost:8000/api/health`. It returns the database status and verifies connectivity.

## Demo deck control

Set the same `DECK_ADMIN_API_KEY` in the API and operator shell, then use the
example concept set to create a reviewable deck:

```sh
uv run python -m scripts.deck_admin generate build-docs/demo-deck-concepts.example.json
uv run python -m scripts.deck_admin list
uv run python -m scripts.deck_admin show <deck-uuid>
uv run python -m scripts.deck_admin activate <deck-uuid>
```

Add `--dry-run` to `generate` or `activate` to validate and display the request
without making an HTTP call or changing data. Generation finishes in `ready`;
only the explicit activation command makes the selected deck playable.

## Repository layout

```text
app/        FastAPI app and shared infrastructure
contracts/  Frozen API, database, data-record, and directory contracts
deckgen/    Nano Banana deck-generation CLI (Phase 3)
worker/     Async cleaning-gauntlet process (Phase 2)
tune/       Isolated Gemma LoRA harness (Phase 4)
frontend/   Arindam's React/Vite application (Phase 5)
data/       Runtime-only local audio, decks, and JSONL corpus shards
```

## Scope discipline

Do not add game behavior to the frontend; it renders the server-owned state contract. Do not change a contract without coordinating both backend and frontend owners.
