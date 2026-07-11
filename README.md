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
- Mobile player UI at `/`, venue TV at `/tv`, operator admin at `/admin`

The frozen integration contracts live in [`contracts/`](contracts/). Living design
notes are in [`Design.md`](Design.md). Agent/coding rules are in
[`AGENTS.md`](AGENTS.md).

## Quick start (Docker demo stack)

Preferred venue/demo path — builds the API, frontend, worker, and migrations
into one image:

1. Copy `.env.example` to `.env`. Set at least:
   - `DATABASE_URL` / Postgres password vars used by Compose
   - `GEMINI_API_KEY` for live decks and gauntlet
   - `DECK_ADMIN_API_KEY` for deck generate/activate and `/admin`
2. Start the stack:
   ```sh
   set -a && source .env && set +a
   docker compose up -d --build
   ```
3. Open:
   - Players: `http://localhost:8000/`
   - Health: `http://localhost:8000/api/health`
   - Venue TV: `http://localhost:8000/tv`
   - Operator admin: `http://localhost:8000/admin`

Runtime blobs stay under `./data` (gitignored). Do not commit audio, decks, or
corpus shards.

### Local API without rebuilding the image

```sh
uv sync --python 3.12 --all-extras
source .venv/bin/activate
docker compose up -d postgres
uv run python -m scripts.apply_schema   # or scripts.apply_migrations
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Matchmaking (demo rules)

Players match when they have **different mother tongues** and at least one
**shared speakable language** (`native_lang` ∪ `common_langs`).

For the demo, when English is in that shared set, the pair’s `common_lang` is
**`en`**, so card / option labels stay in English. Both players should include
English in “what else do you speak” for the intended stage path.

Queue rows older than ~30s without a `POST /api/pair/request` heartbeat are
evicted. Nicknames are case-insensitively unique.

## Demo deck control

Whimsical regional Nano Banana decks (not centered product shots). Set the same
`DECK_ADMIN_API_KEY` in the API and operator shell:

```sh
uv run python -m scripts.deck_admin generate build-docs/demo-deck-concepts.example.json
uv run python -m scripts.deck_admin list
uv run python -m scripts.deck_admin show <deck-uuid>
uv run python -m scripts.deck_admin activate <deck-uuid>
```

Add `--dry-run` to `generate` or `activate` to validate without changing data.
Generation finishes in `ready`; only explicit activation makes a deck `live`.
Published image files use the real encoding extension (`.jpg` / `.png` / `.webp`)
from Gemini’s bytes.

Operator UI: paste the admin key at `/admin` for decks, metrics, and redacted
traces. Per-utterance stage walks stay on the CLI:

```sh
uv run python -m scripts.pipeline_view --fixture
# or --turn-id <uuid>
```

See [`phase-plan/wave-3-launch-demo/ADMIN-DEMO-RUNBOOK.md`](phase-plan/wave-3-launch-demo/ADMIN-DEMO-RUNBOOK.md).

## Repository layout

```text
app/        FastAPI app, game core, Gemini client, admin APIs
contracts/  Frozen API, database, data-record, and directory contracts
deckgen/    Nano Banana deck-generation CLI
worker/     Async cleaning-gauntlet process
tune/       Isolated Gemma LoRA harness
frontend/   React/Vite player + TV + /admin surfaces
scripts/    Schema, deck admin, pipeline view, bootstrap helpers
build-docs/ Briefs, architecture notes, demo concept JSON
phase-plan/ Wave orchestration and runbooks
data/       Runtime-only local audio, decks, and JSONL corpus shards
```

## Scope discipline

Do not add game behavior to the frontend; it renders the server-owned state
contract. Do not change a contract without coordinating both backend and
frontend owners. Keep Gemini model IDs in `app/models.py` and prompts in named
modules (`deckgen/prompts.py`, `worker/prompts.py`).
