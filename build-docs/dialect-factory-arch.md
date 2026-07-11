# Dialect Data Factory — Architecture & Orchestrator Phase Plan
**Stack locked:** SCAR 18 laptop (RTX 5090) · FastAPI (uvicorn, 2–4 workers) · **Postgres 16 in Docker** · local disk for blobs · cloudflared tunnel · client polling (2s) · React/Vite static build served by FastAPI · Gemini API for triage/decks · local LoRA on Gemma 4 E2B.

**Planning doc only. No code before 10:30 AM. Repo goes public at 10:30 sharp.**

---

## 1. System architecture

```
                         ┌──────────────────────────────────────────────┐
                         │            SCAR 18 (venue laptop)            │
  Player phones          │                                              │
  (mobile web, QR join)  │  ┌────────────────────────────────────────┐  │
        │                │  │  FastAPI (uvicorn, 2–4 workers,        │  │
        │ HTTPS          │  │  asyncpg pool per worker)              │  │
        ▼                │  │                                        │  │
  cloudflared tunnel ────┼─▶│  /api/join  /api/state (poll)          │  │
  (named tunnel,         │  │  /api/turn/audio  /api/turn/guess      │  │
   stable URL for QR)    │  │  /api/leaderboard  /api/metrics        │  │
                         │  │  /            → serves React build     │  │
                         │  └───────┬──────────────┬─────────────────┘  │
                         │          │              │                    │
                         │          ▼              ▼                    │
                         │   Postgres 16       /data/audio/*.webm       │
                         │   (Docker,          /data/decks/*.png        │
                         │    volume-mounted)  /data/corpus/*.jsonl     │
                         │          ▲                                   │
                         │          │                                   │
                         │  ┌───────┴────────────────────────────────┐  │
                         │  │ Gauntlet worker (separate process,     │  │
                         │  │ claims from `jobs` table via           │  │
                         │  │ FOR UPDATE SKIP LOCKED, 1s poll):      │  │
                         │  │  • audio triage (Gemini 3.5 Flash)     │  │
                         │  │  • contamination check                 │  │
                         │  │  • dedup hash                          │  │
                         │  │  • golden-record packaging → jsonl     │  │
                         │  └───────┬────────────────────────────────┘  │
                         │          │ Gemini API (outbound HTTPS)       │
                         │          ▼                                   │
                         │  ┌────────────────────────────────────────┐  │
                         │  │ Deck engine (separate script):         │  │
                         │  │ NB2 Lite gen + Gemini label-           │  │
                         │  │ consistency check → writes DB direct   │  │
                         │  └────────────────────────────────────────┘  │
                         │  ┌────────────────────────────────────────┐  │
                         │  │ LoRA harness (fully separate process,  │  │
                         │  │ reads /data/corpus, never touches DB): │  │
                         │  │ Gemma 4 E2B + Unsloth/PEFT on 5090     │  │
                         │  └────────────────────────────────────────┘  │
                         └──────────────────────────────────────────────┘
```

**Hard rules baked into the architecture:**
- **Postgres in Docker, volume-mounted data dir.** `docker compose up -d` is step one of Phase 0; compose file lives in the repo. No lock contention worries — multiple uvicorn workers and the gauntlet worker all write freely.
- **Jobs table with `FOR UPDATE SKIP LOCKED`.** Gauntlet work queued as rows; multiple worker processes can claim safely if you need to scale mid-event. Survives restarts, inspectable with one `psql` command when something looks wrong at 2 PM.
- **Gauntlet is async, gameplay is not blocked by it.** B can guess as soon as audio lands; triage/contamination run in the background and can retro-flag a record out of the training set. Player-facing re-record prompts only come from fast checks (duration, silence) done at upload time.
- **Corpus is append-only JSONL shards** (`corpus/shard_NNNN.jsonl` + audio refs), flushed by the gauntlet worker only (single flusher avoids shard write races). The LoRA harness consumes this directory with zero manual steps — the "no human touched the data" line depends on this seam.
- **Tunnel:** cloudflared **named tunnel** (create tunnel + DNS tonight — config, not code; run it at 10:30, print QR immediately). Fallback: ngrok with reserved domain. Test phone → tunnel → laptop before writing any game logic.
- **Wi-Fi risk:** laptop on venue Wi-Fi + phone hotspot as backup uplink for the tunnel. Players use their own data/venue Wi-Fi — nothing venue-LAN-dependent.

---

## 2. Data model (Postgres)

```
players(id, nickname, native_lang, common_langs jsonb, session_token, created_at)
pairs(id, player_a, player_b, common_lang, status, created_at)
decks(id, region_tag, status)                    -- generated by deck engine
cards(id, deck_id, image_path, label_common jsonb, decoys jsonb, verified bool)
turns(id, pair_id, speaker_id, guesser_id, card_id, status,
      audio_path, duration_s, attempts, outcome,  -- validated|unclear|pending
      created_at)
jobs(id, kind, payload jsonb, status, tries, last_error, created_at)
                                                  -- kind: triage|contam|package
records(id, turn_id, golden jsonb, training_eligible bool, shard_file)
metrics_counters(key, value)                      -- throughput instrumentation
```

Golden record JSON = exactly the schema in the handoff doc §4. `records.golden` is the single source of truth; JSONL shards are flushed from it. Use jsonb throughout — free flexibility during a hackathon, and `psql` queries against it are your debugger.

---

## 3. API surface (freeze this early — it's the Arindam contract)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/join` | POST | nickname + native lang + common langs → session token |
| `/api/state` | GET | **the one polling endpoint.** Returns full view-state for this player: pairing status, whose turn, card (image or options grid), timer, scores. Client renders purely from this. |
| `/api/pair/request` | POST | enter matchmaking queue |
| `/api/turn/audio` | POST | multipart webm/opus upload; fast checks inline (duration 1–8s, non-silence); returns ok / re-record |
| `/api/turn/confirm-label` | POST | A confirms label *after* audio accepted (enforces image-before-label server-side) |
| `/api/turn/guess` | POST | B's pick; server scores, advances turn |
| `/api/leaderboard` | GET | top N |
| `/api/metrics` | GET | validated pairs, langs, per-sample cost, gauntlet pass rates — feeds the pitch numbers |

**Polling contract:** client polls `/api/state` every 2s; server responses include `state_version` so the client can skip re-render. One endpoint, one render function — this is what keeps Arindam's side simple.

**Matchmaking:** queue in DB; match = shared common lang ∧ different native lang ∧ not previously paired (relax the last constraint if pool is thin). Claim matches inside a transaction with `SKIP LOCKED` so concurrent workers never double-pair. Odd player waits with a "recruiting" screen that shows the leaderboard.

**Deck engine writes the DB directly** (Postgres makes this safe) — no upload endpoint needed; it just inserts verified cards inside a transaction and flips `decks.status = 'live'` last.

---

## 4. Phase plan for orchestrator + subagents

Design principle: **Phase 0 freezes all contracts** (schema, API spec, golden record, dir layout) so Phases 1–4 run as parallel subagents with no cross-talk. Each phase below is a self-contained work order: inputs, outputs, done-when.

### Phase 0 — Contracts & skeleton (orchestrator itself, ~30 min, blocks everything)
- Repo public, license, README stub with track framing (Track 3 + Gemma prize flags).
- `docker-compose.yml` (Postgres 16 + volume), `.env.example`.
- `contracts/`: `schema.sql` (applied via migration script), `openapi.yaml` (or typed `api_types.py` + generated TS types), `golden_record.md`, `dirs.md`.
- FastAPI skeleton boots against Postgres, serves a static `index.html`, `/api/health` does a `SELECT 1` and works through the tunnel from a phone. **Done-when: QR on a phone hits the laptop and health shows DB connected.**

### Phase 1 — Game core (subagent A, backend)
- Depends: Phase 0. Parallel with 2, 3, 4.
- Implements: join, matchmaking (transactional, SKIP LOCKED), turn state machine (elicit → confirm-label → guess → score), `/api/state` composer, leaderboard, scoring rules (points on validation only), per-player caps.
- Fast inline audio checks at upload (duration, RMS silence check) → re-record response.
- **Done-when:** two phones can play a full round end-to-end with a hardcoded 5-card deck and no Gemini calls.

### Phase 2 — Cleaning gauntlet worker (subagent B, backend)
- Depends: Phase 0 (jobs table + record schema). Parallel with 1.
- Separate process. Loop: claim job (`FOR UPDATE SKIP LOCKED`) → Gemini 3.5 Flash triage (speech? single speaker? SNR) → contamination check (is it just the label read aloud?) → dedup hash (audio fingerprint per player) → write `records` row + flush to JSONL shard → bump metrics counters.
- Retries with backoff, `last_error` captured, poison jobs parked after 3 tries. Horizontally scalable: run a second worker process if backlog grows — SKIP LOCKED makes this free.
- **Done-when:** dropping a webm + fake turn row into the pipeline produces a golden JSONL line with correct flags, and a "label read aloud" sample gets contamination-flagged.

### Phase 3 — Deck engine (subagent C, can be Arindam's second task or shared)
- Depends: Phase 0 (schema). Fully parallel.
- Script: prompt strategy (from handoff doc) → NB2 Lite batch generation → Gemini image↔label consistency check → decoy selection (semantically near, not identical) → insert verified cards, flip deck live.
- Instrument: images/min, cost/image, reject rate — **this is the Track 3 demo material; log it loudly.**
- **Done-when:** one command produces a 30-card verified regional deck in <5 min and it appears in gameplay.

### Phase 4 — LoRA harness (subagent D, isolated process)
- Depends: golden record contract only. Fully parallel; test on dummy corpus by 2 PM.
- Unsloth/PEFT, Gemma 4 E2B, consumes `/data/corpus/*.jsonl`, produces adapter + a `compare.py` (base vs tuned on 5 held-out samples) for Tier 2 theatre.
- VRAM budget note: E2B LoRA fits comfortably in 24 GB alongside nothing else — but Postgres and the API are CPU/RAM, so no GPU contention until the tune runs. Keep batch small; it only needs to finish once by ~4:40.
- **Done-when:** dummy-corpus tune completes end-to-end and `compare.py` prints side-by-sides.

### Phase 5 — Frontend (Arindam, parallel from Phase 0)
- Pure render of `/api/state` + three POSTs. Push-to-talk via MediaRecorder (webm/opus), image-before-label sequence enforced by both UI and server. Big-text mobile-first, leaderboard screen for the venue TV (a *screen*, not the product — mind the "dashboard as main feature" ban).
- **Done-when:** plays against Phase 1 backend on real phones.

### Phase 6 — Integration & load sanity (orchestrator, ~2:30–3:30 PM)
- Real deck swapped in, gauntlet running against live play, metrics endpoint feeding pitch numbers, 20-phone smoke via a tiny polling-storm script (200 fake clients hitting `/api/state`).
- Insurance corpus recorded (50–100 Assamese samples through the real game UI — rules-legal, during hours).

### Cut order (unchanged from handoff): on-device inference → Live API flourish → leaderboard polish → Tier 2. Never cut: game loop, gauntlet, throughput instrumentation, deck engine.

---

## 5. Failure modes & pre-decided responses

| Failure | Response (decided now, not at 2 PM) |
|---|---|
| Venue Wi-Fi dies | Tunnel rides phone hotspot; players on own data. QR URL unchanged (named tunnel). |
| Gemini quota/latency spike | Gauntlet is async — gameplay unaffected; jobs backlog drains later (spin up a second worker). Fast inline checks keep re-record UX alive without Gemini. |
| Docker/Postgres hiccup | Volume-mounted data survives container restart; `docker compose up -d` recovers in seconds. Health endpoint surfaces DB state. |
| Deck engine slow | Ship with the first 30-card deck; rotate decks when ready. |
| Fine-tune fails | Tier 2 was never promised; Tier 1 metrics stand alone. |
| Laptop dies | git push after every phase; `pg_dump` + /data rsync'd to the MacBook every 15 min (cron/scheduled task). |

---

## 6. What the orchestrator should generate first on Saturday
1. `docker-compose.yml` + `contracts/` files (from this doc, verbatim where possible)
2. Phase 0 skeleton
3. Then spawn subagents A–D with: this doc §relevant-phase + `contracts/` as their full context. No subagent needs the whole repo in context — contracts are the interface.

---

## 7. Model intel (verified July 11, 2026 — read before writing any API call)

### Gemini 3.5 Flash (`gemini-3.5-flash`) — gauntlet brain
- GA/stable. Text+image+video+audio+PDF **input, text output only** — no image gen (that's NB2 Lite's job), no Live API, no Computer Use.
- 1M input / 65K output context. Pricing $1.50/M in, $9/M out — pricier than old Flash; irrelevant on hackathon credits but don't leave thinking on high.
- **Set `thinking_level: "low"` (or minimal) on triage/contamination calls.** Default is medium; our checks are simple classification and latency matters for backlog drain.
- Structured outputs + JSON mode work with tools — use a strict response schema for triage verdicts (`{is_speech, single_speaker, snr_ok, is_label_readout, confidence}`), one call per utterance combining triage + contamination.
- Audio input: via Files API (upload → URI) or inline base64 (total request ≤ 20 MB — trivially fine for 1–8s clips; use inline, skip Files API round-trip).
- **⚠️ Audio format risk:** Firebase-side docs list `audio/webm` and `audio/opus` as supported, but the core Gemini API docs historically list WAV/MP3/AIFF/AAC/OGG/FLAC, and there are recent reports of Opus rejections on some endpoints. **Decision: gauntlet worker transcodes webm/opus → 16 kHz mono FLAC via ffmpeg before every Gemini call.** ~10 lines, removes the whole risk class, and FLAC is also the cleanest archival format for the corpus. Browser still records webm/opus (universal MediaRecorder support); we keep both files.
- Temp Google accounts are provisioned day-of with unknown rate limits → every Gemini call goes through one thin client wrapper with retry + exponential backoff + a per-minute semaphore we can tune live.

### NB2 Lite (`gemini-3.1-flash-lite-image`) — deck engine
- Released June 30, 2026 (11 days old — expect rough edges, keep retries). Text-to-image + editing + multi-image composition; ~4s per image; 1K resolution cap; 14 aspect ratios; SynthID watermark on outputs.
- **⚠️ Pricing correction: it's ~$0.034 PER IMAGE ($0.0336 official, $0.25/M input + $1.50/M output tokens), not per 1,000 images.** The event PDF's "$0.034 per 1,000 images" is a typo. Fix the unit-economics slide before the pitch — a 30-card deck ≈ $1.30 with rejects, still an excellent "price of a biscuit packet" number per validated sample, but don't get caught quoting 1000× wrong on stage.
- Known weaknesses: small text rendering, character consistency across scene changes, **no Search grounding** (Nano Banana 2 has it; Lite doesn't). None of these hurt us — our cards are single objects/scenes with no text. Keep card prompts text-free and single-subject.
- Benchmarks favor it for exactly our use (T2I Elo 1251, above even NB Pro) — quality is fine for game cards.
- Verification loop stands: NB2 Lite generates → Gemini 3.5 Flash checks image↔label agreement → reject/regen. Budget ~20–30% reject rate in throughput math.

### Gemma 4 (E2B / E4B) — fine-tune target
- **Big finding: E2B and E4B are natively multimodal including AUDIO input.** This upgrades the Tier 2 story from text→text translation to **dialect speech → common-language text**, directly on the corpus with no transcription step. That's a dramatically better demo if it works.
- **Unsloth's own guidance: prefer E4B QLoRA over E2B LoRA** (quality gap big, quant loss minuscule). E4B QLoRA fits in 16 GB; the 5090's 24 GB is comfortable. Use `use_gradient_checkpointing="unsloth"`, rank 16, lora_dropout 0, batch 1–2 + grad accum.
- Unsloth has day-0 Gemma 4 support and documented **vision** fine-tuning for E2B/E4B; **audio fine-tuning support is the one unverified link in the chain.** Plan: subagent D attempts audio-in SFT first (dataset rows: audio path + target common-lang text, Gemma 4 chat template). Fallback (pre-decided): text→text tune on `gemini-transcribed dialect text → common text` pairs — weaker story, still a live same-day tune.
- Tonight (docs-reading is legal pre-work): read Unsloth's Gemma 4 multimodal dataset format page so the audio-SFT attempt isn't a cold start at 1 PM.
- Export path if on-device stretch survives: merge LoRA → GGUF via llama.cpp for local inference. E2B runs in ~5 GB (4-bit) — phone-plausible, laptop-trivial.
- 140-language pretraining coverage is the pitch-friendly fact: Gemma 4 has seen the *major* Indian languages; our corpus teaches it the *dialects* the pretraining never had.

### Cross-cutting API decisions (bake into `contracts/`)
1. One `gemini_client.py` wrapper: retry/backoff, per-model semaphores, request/response logging to Postgres (`api_calls` table — becomes cost instrumentation for free).
2. ffmpeg transcode step is part of the gauntlet contract: store `audio_raw.webm` + `audio_clean.flac` per utterance; Gemini and the corpus both use the FLAC.
3. Pin exact model strings in one `models.py` constants file: `gemini-3.5-flash`, `gemini-3.1-flash-lite-image`, `unsloth/gemma-4-e4b` (or `-e2b` fallback). No string literals anywhere else.
4. Pre-pull Unsloth Docker/pip deps and the Gemma 4 E4B weights **tonight** — environment setup isn't hackathon work product, and HuggingFace downloads on venue Wi-Fi are how timelines die.

---

## 8. Subagent work orders (paste-ready)

Common preamble for every subagent (orchestrator prepends this):

> You are building one component of "Dialect Data Factory," a hackathon project due at 5 PM today. You have: `contracts/schema.sql`, `contracts/api_types.py`, `contracts/golden_record.md`, `contracts/dirs.md`, and this work order. Do not modify contracts — if a contract blocks you, stop and report; the orchestrator arbitrates. Do not touch files outside your listed paths. Working code over elegant code; every hour matters. Write a smoke test for your done-when criterion and run it before reporting done. Model strings come from `app/models.py` only.

### Work order A — Game core (`app/api/`, `app/game/`)
> Build the FastAPI game backend per `contracts/api_types.py`. Deliverables: (1) `/api/join` issuing session tokens; (2) matchmaking: queue table, match on different declared native language ∧ non-empty intersection of speakable sets (`native_lang` ∪ `common_langs`), prefer `en` as `common_lang` when shared, claim matches in a transaction with `FOR UPDATE SKIP LOCKED`, never pair a player with themselves or (if pool allows) a repeat partner; (3) turn state machine with states `awaiting_audio → awaiting_label_confirm → awaiting_guess → scored`, enforced server-side — the label must never be returned to speaker A before their audio is accepted; (4) `/api/state`: single composed view per player with `state_version`, cheap enough for 2s polling from 200 clients (one round-trip to PG, no N+1); (5) `/api/turn/audio`: accept multipart webm, run inline fast checks only (duration 1–8s via container metadata, RMS silence check), save to `/data/audio/{utterance_id}.webm`, insert a `triage` job row, return `ok` or `re_record` with a playful reason string; (6) `/api/turn/guess`: score validated-only, decoys from `cards.decoys`, two wrong guesses → outcome `unclear`, advance turn; (7) leaderboard + per-player session caps from `config.py`. Done-when: `tests/smoke_game.py` simulates two players completing 3 full rounds against a seeded 5-card deck with zero Gemini calls.

### Work order B — Cleaning gauntlet (`worker/`)
> Build a standalone worker process (own entrypoint, own PG pool, no FastAPI imports). Loop: claim oldest pending job via `FOR UPDATE SKIP LOCKED`; for `triage` jobs: (1) ffmpeg-transcode the webm to 16 kHz mono FLAC at `/data/audio/{id}.flac`; (2) one Gemini 3.5 Flash call (through `app/gemini_client.py`, `thinking_level: low`, strict JSON response schema) returning `{is_speech, single_speaker, snr_ok, is_label_readout, confidence}` — the contamination question is: "is this utterance merely the provided label text read aloud, versus a description in a different language?" Pass the card's label text in the prompt; (3) compute a dedup fingerprint (chromaprint or MFCC-hash) and check against the speaker's prior utterances; (4) write the golden record per `contracts/golden_record.md` with `training_eligible` computed from all gates, append to the current JSONL shard (you are the only shard writer; rotate at 500 records), bump `metrics_counters`. Retries: 3 with exponential backoff, then park with `last_error`. Done-when: `tests/smoke_gauntlet.py` feeds (a) a clean recording, (b) a silent file, (c) a label-read-aloud recording, and asserts eligible/rejected/contaminated respectively.

### Work order C — Deck engine (`deckgen/`)
> Build a CLI: `python -m deckgen --region assam --cards 30`. Pipeline per card: (1) pick a concept from the curated concept list (`deckgen/concepts.py` — everyday Indian domestic/rural/market objects and scenes, no text in image, single clear subject); (2) generate via NB2 Lite (`gemini-3.1-flash-lite-image`) with a prompt template enforcing: photographic, culturally grounded to the region parameter, no words/signage/labels visible, one dominant subject; (3) verify with Gemini 3.5 Flash: "does this image unambiguously depict {label}? yes/no + confidence" — reject < threshold and regenerate (max 2 retries per card); (4) pick 5 decoys from other concepts in the same deck, semantically adjacent but visually distinct (Gemini call, batch it); (5) insert cards in one transaction, flip `decks.status='live'` last. Instrument loudly: log and store images/min, $/image, reject rate — this is the Track 3 demo material and will be read on stage. Done-when: one command yields a 30-card live deck in under 5 minutes and cards render in the game.

### Work order D — LoRA harness (`tune/`)
> Standalone scripts, no imports from `app/` or `worker/`; your only interface is `/data/corpus/*.jsonl` + `/data/audio/*.flac`. Deliverables: (1) `tune/prepare.py`: read shards, filter `training_eligible`, hold out 20% stratified by `native_lang_tag`, emit Unsloth-format dataset — **attempt audio-in SFT first** (rows: FLAC path + target common-language text, Gemma 4 chat template, task framing "translate this speech to {common_lang}"); if Unsloth's audio dataset path is broken or undocumented in practice, fall back to text→text (add a Gemini transcription step) and report which path you shipped; (2) `tune/train.py`: Unsloth QLoRA on `gemma-4-e4b`, rank 16, `use_gradient_checkpointing="unsloth"`, batch 1 + grad-accum 8, ~3 epochs on a small corpus, must complete < 25 min on a few hundred samples on the RTX 5090; (3) `tune/compare.py`: 5 held-out samples, base vs. tuned, side-by-side stdout formatted for a stage screenshot; (4) `tune/metrics.py`: exact/fuzzy match on the full holdout, written to a JSON file — shown to no one unless the orchestrator says so (Tier 3 policy). Done-when: full pipeline runs end-to-end on the dummy corpus (`tune/make_dummy.py`, 100 synthetic rows) by 2 PM.

### Orchestrator kickoff checklist (10:30–11:00)
1. `git init` → public repo → push README with track framing. 2. `docker compose up -d` (Postgres). 3. Apply `contracts/schema.sql`. 4. FastAPI skeleton + `/api/health` (`SELECT 1`). 5. `cloudflared tunnel run` → phone test → hand QR PNG to Arindam for printing. 6. Spawn A–D with preamble + work order + contracts. 7. Set a 12:30 integration checkpoint: A's smoke test green is the gate for Arindam's frontend hookup.
