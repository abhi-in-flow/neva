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

On the speaker confirm-label phase, the UI presents the system card label as
the target concept the player was describing ("Your target concept was:" /
"Did your recording describe this?"), not as an ASR or speech translation.
The label still arrives only after a successful audio upload.

Matchmaking remains active while the backend reports onboarding or queued.
Successful queued responses retry with bounded jitter, closing the concurrent
`SKIP LOCKED` enqueue race without overlapping requests. Same-native pairing
remains forbidden; compatibility uses the intersection of each player's
speakable set (`native_lang` ∪ `common_langs`) so a partner who lists the
other's mother tongue as a known language can still match. When English is in
that shared set, the pair's `common_lang` prefers `en` so demo card labels stay
in English. The waiting screen explains that players need different native
languages and at least one shared known language. Its QR only shares the
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

Join and Queued UIs show readable dual-script labels for the player's mother
tongue and known languages (for example `Assamese · অসমীয়া`) so venue demos
can confirm selections at a glance.

### Shared Gemini client resilience

Transient HTTP disconnects (`httpx.TransportError`, including
`RemoteProtocolError`) are treated as retryable. Structured-output schemas are
sanitized for the Gemini Developer API (no `additionalProperties`; nullable
fields use SDK-compatible shapes).

### Deck engine

Generates culturally grounded picture cards with Nano Banana 2 Lite, verifies
image-label consistency with Gemini, chooses decoys, and publishes complete
decks. The CLI and advanced JSON admin path still publish **atomically**. The
primary admin prompt-to-deck path publishes **progressively**: each verified
card is persisted while status stays `generating`; up to four Nano Banana
image requests run concurrently, with verification and short serialized
publishing after each completion. Decoys are then backfilled and status becomes
`ready`. Generation speed, cost, rejection rate, and
`progress_stage` / `cards_ready` / `cards_target` are first-class demo metrics.

Region tags include all 28 Indian states (lowercase hyphenated) plus legacy
aliases (`bengal`, `bangalore`, `north`, `northeast`, `tamil`, …).

Card images are prompted as whimsical, visibly absurd scenes in authentic
Indian regional settings (Assam / Northeast emphasis in the curated pool and
demo concepts file), not centered studio product shots. Each card still has
one unmistakable target concept or short action phrase so charades stays
instantly guessable on a phone. Verification rejects missing humor, text,
Western stock framing, and harmful stereotypes without changing the verify
JSON schema. Live image calls request square ``1:1`` composition when the
shared Gemini client supports native image config. Default curated concepts
in ``deckgen/concepts.py`` and operator input via
``build-docs/demo-deck-concepts.example.json`` (``--concepts-file`` /
``scripts.deck_admin generate``) both supply scene-level funny situations,
including a pink-elephant gag.

Publish writes filenames from image magic bytes (``.png``, ``.jpg``, or
``.webp``). Nano Banana often returns JPEG even when operators expect PNG;
extension mismatch is treated as a publisher bug, not a model failure.

### Operator deck control

A demo-safe administration boundary lets an operator replace the generation
concept set without editing code. The **primary** path is prompt-to-deck:
the operator enters a one-line theme and an Indian state (or an example
prompt). `POST /api/admin/decks/from-prompt` immediately creates a
`generating` deck and returns 202. Background work invents concepts with
Gemini Flash (never returned to the browser while generating), then Nano
Banana 2 Lite generates up to four images concurrently. Progressive publish persists each verified
card row as soon as it is ready, updates `generation_metrics` with
`progress_stage` / `cards_ready` / `cards_target` for the review UI, runs
batch decoy selection, backfills decoys, and only then sets status to
`ready`. On failure the deck is marked `failed` and partial cards are kept
for diagnostics. Activation of `generating` / `failed` remains rejected.

The advanced fallback still accepts explicit concept JSON via
`POST /api/admin/decks` (`AdminDeckGenerateRequest`) and publishes
atomically to `ready`. Each submitted concept has a stable ID, English
label, locale, and cultural hint.

Activation is explicit and serialized. Activating a ready deck atomically
demotes the previous live deck to ready and promotes the selected deck to
`live`; game card selection therefore continues to consume the existing
`status = 'live'` contract while using exactly one operator-selected deck.
The API uses a demo-only `X-Deck-Admin-Key`, and the matching CLI supports
no-network dry runs for generation and activation commands.

### Operator admin web surface

The React build also serves a separate `/admin` root (pathname fork beside
`/tv`). After the operator pastes the shared admin key into sessionStorage,
the surface provides:

1. **Decks** — primary prompt + Indian-state form with example themes;
   progressive review grid (skeletons + images as cards appear), click-to-open
   image modal, prominently highlighted live/final estimated cost, and explicit
   activate. JSON concept upload remains under a collapsed Advanced section.
2. **Metrics** — poll public `GET /api/metrics`, deck `generation_metrics`,
   and protected aggregate eligibility funnel counts.
3. **Traces** — protected reads of redacted `api_calls`, worker heartbeats,
   and gauntlet jobs. Prompt text, audio, and secrets are never returned to
   the browser.
4. **Tune** — static terminal runbook only; Gemma train/compare stays in
   `tune/demo.py` and never touches Postgres.

The surface carries a short privacy notice: no personal information is
requested, submitted audio is retained, and Gemma 4 training plus demo hosting
run locally on the operator machine.

Per-utterance WebM→FLAC→gate walkthroughs remain on the operator CLI
(`python -m scripts.pipeline_view`) so participant audio stays off the web UI.

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

Operators run `prepare` → `train` → `compare` against `data/corpus` (real
eligible speech) or a synthetic fixture with the same CLI. For small live
corpora, `tune/run-real-demo.sh` applies demo-tuned `TUNE_EPOCHS` /
`TUNE_GRAD_ACCUM` so the adapter visibly diverges from base on holdout without
claiming generalization. Stage optional live-mic uses `tune/demo.py` plus
`tune/capture_demo_audio.ps1`; neither path mutates the append-only corpus.

## Contracts

The frozen integration contracts are:

- `contracts/schema.sql` — Postgres schema
- `contracts/api_types.py` — backend/frontend API shapes
- `contracts/golden_record.md` — canonical corpus record and eligibility
- `contracts/dirs.md` — local runtime storage layout

## Runtime

- WSL2/Linux preferred for the repo (native filesystem, Bash, `uv` + Python 3.12)
- Postgres 16 in Docker; API/worker/frontend via `docker compose`
- FastAPI/Uvicorn
- Local blob storage under `data/`
- Optional public HTTPS access through a named Cloudflare tunnel

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
- `/admin` web surface for decks, metrics, and redacted model traces, plus
  `scripts.pipeline_view` for sanitized per-utterance stage walks;
- isolated audio-first Gemma E4B QLoRA scaffolding and explicit text fallback.

Arindam's frontend remains independently owned and can now switch from mock
fixtures to the real API. Confirm-label re-record is an accepted temporary UX
cut. Real Gemini deck generation, real gauntlet audio, and real LoRA training
remain explicit go/no-go operations rather than test-suite assumptions.
