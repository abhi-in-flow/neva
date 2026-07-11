# Dialect Data Factory — Design

This document is the living feature and architecture record for the app.
Update it whenever a feature, user flow, contract, or system boundary changes.

## Product

Dialect Data Factory is a multiplayer, turn-based charades game that produces
validated dialect speech data. A speaker describes a system-known image in
their native language. A partner who does not share that native language hears
the recording and selects its meaning in a shared language. Correct guesses
validate the speech-to-label pair without manual annotation.

## Core invariants

1. The image appears before its label; the label is unavailable until audio is
   accepted.
2. The backend is the source of truth for game phases, scoring, and visibility.
3. Points are awarded only for validated pairs.
4. Cleaning runs asynchronously and cannot block gameplay.
5. Only records passing machine quality checks, human validation,
   contamination checks, and de-duplication enter the training corpus.
6. Runtime data is local, append-only where practical, and excluded from Git.

## Components

### FastAPI application

Owns player sessions, matchmaking, turn progression, state composition,
leaderboards, metrics, and static frontend delivery. Postgres is its durable
coordination layer.

### Shared Gemini client

`app/gemini_client.py` centralizes authentication, canonical model validation,
per-model throttling, retry/backoff, structured outputs, redacted request and
response logs, and best-effort `api_calls` instrumentation. The gauntlet adapts
small FLAC inputs through `generate_content`. The deck engine uses native
Gemini image generation through `generate_content` with IMAGE response
modality; the Imagen-only `models.generate_images` endpoint is not used for
Nano Banana.

### Frontend

A mobile-first React/Vite client owned by Arindam. It polls one state endpoint,
renders the server-provided phase, records audio, and sends actions. It does
not implement game rules.

Matchmaking remains active while the backend reports onboarding or queued.
Successful queued responses retry with bounded jitter, closing the concurrent
`SKIP LOCKED` enqueue race without overlapping requests. Same-native pairing
remains forbidden; the waiting screen explains that players need different
native languages and at least one shared known language. Its QR only shares the
onboarding URL—matchmaking itself remains automatic and backend-owned.

Queue liveness uses `POST /api/pair/request` as the heartbeat: every enqueue
refreshes `matchmaking_queue.enqueued_at`. Before a match claim, the backend
evicts rows older than the configured activity TTL (default 30s, comfortably
above the ~2s frontend retry interval) inside the same matchmaking
transaction, so abandoned demo/test players cannot be selected. Already-active
pairs are returned immediately and are not harmed by queue eviction.

Player nicknames are case-insensitively unique. Join atomically reserves the
requested friendly name when free; collisions append a compact `#N` suffix
bounded to the 32-character schema/API limit. Uniqueness is enforced by a
Postgres unique index on `lower(nickname)` plus insert-retry on unique
violations (never a read-then-write race). The join response payload shape is
unchanged; clients observe the persisted nickname via `/api/state`.

### Deck engine

Generates culturally grounded picture cards with Nano Banana 2 Lite, verifies
image-label consistency with Gemini, chooses decoys, and publishes complete
decks atomically. Generation speed, cost, and rejection rate are first-class
demo metrics.

### Operator deck control

A demo-safe administration boundary lets an operator replace the generation
concept set without editing code. Each submitted concept has a stable ID,
English label, locale, and cultural hint. The protected admin API immediately
creates a `generating` deck and runs Gemini image generation in a background
task. Successful generation stops at `ready`, where operators can inspect
concepts, labels, verification state, images, and generation metrics.

Activation is explicit and serialized. Activating a ready deck atomically
demotes the previous live deck to ready and promotes the selected deck to
`live`; game card selection therefore continues to consume the existing
`status = 'live'` contract while using exactly one operator-selected deck.
The API uses a demo-only `X-Deck-Admin-Key`, and the matching CLI supports
no-network dry runs for generation and activation commands.

### Cleaning gauntlet

A standalone worker transcodes browser audio to FLAC, runs Gemini speech and
contamination checks, detects duplicates, packages golden records, and appends
eligible records to JSONL shards.

Machine and human gates are deliberately decoupled. Audio acceptance enqueues
`triage`, which stores quality metadata on the turn. Once that metadata exists
and the turn is scored, either the game or worker idempotently enqueues
`package`. Only `package` creates the canonical record and appends eligible
data, so a fast worker can never package before the partner's validation.

### Fine-tuning harness

An isolated optional pipeline that consumes corpus shards and clean audio. It
does not access the application database. Failure or weak evaluation results
must not affect the primary throughput demo.

## Contracts

The frozen integration contracts are:

- `contracts/schema.sql` — Postgres schema
- `contracts/api_types.py` — backend/frontend API shapes
- `contracts/golden_record.md` — canonical corpus record and eligibility
- `contracts/dirs.md` — local runtime storage layout

## Runtime

- Windows host with PowerShell
- Python 3.12 managed by `uv`
- Postgres 16 in Docker
- FastAPI/Uvicorn
- Local blob storage under `data/`
- Public HTTPS access through a named Cloudflare tunnel

## Delivery orchestration

Implementation is organized under `phase-plan/` into four gated waves:

1. Foundation freezes contracts and verifies the Windows runtime.
2. Game, gauntlet, deck, tuning, and frontend work proceed in parallel against
   those contracts.
3. Integration, load, data-quality, and recovery work validate the assembled
   system using isolated data.
4. Launch monitoring, evidence review, rehearsal, and packaging produce the
   public pilot and submission.

Each wave has one orchestrator. Subagents receive exclusive path ownership and
self-contained work orders; exploration agents remain read-only. Shared
contracts and cross-component seams are arbitrated only by the wave
orchestrator. A wave advances only after its acceptance gate is recorded as
`PASS` or an explicit `PASS WITH CUTS`.

## Current implementation status

Wave 0 is `PASS WITH CUTS`: the Windows runtime, contracts, schema/migrations,
health endpoint, and local Cloudflare connector are verified; publishing the
stable hostname is deferred until after Wave 1.

Wave 1 backend work is implemented and verified:

- game routers, bearer sessions, matchmaking, turn/scoring state machine,
  leaderboard, metrics, and no-label-leak tests;
- shared Gemini client plus gauntlet audio adapter;
- standalone triage/package worker with retries and corpus sharding;
- regional deck CLI with dry-run fakes, verification, decoys, atomic publish,
  and throughput/cost metrics;
- operator-supplied deck concepts, protected generation/review/activation API,
  and a dry-run-capable administration CLI;
- isolated audio-first Gemma E4B QLoRA scaffolding and explicit text fallback.

Arindam's frontend remains independently owned and can now switch from mock
fixtures to the real API. Confirm-label re-record is an accepted temporary UX
cut. Real Gemini deck generation, real gauntlet audio, and real LoRA training
remain explicit go/no-go operations rather than test-suite assumptions.
