# Neva

Turn a multiplayer charades game into a pipeline for validated speech→concept
pairs.

**Neva** combines each audio utterance with the deck-owned concept label and a
human partner's confirmation that the meaning landed—no transcript and no
separate annotation pass. Strangers with different mother tongues describe
Nano Banana 2 Lite–generated regional picture decks, partners guess the concept
in a shared language, and automated quality gates clean the accepted audio
before eligible records enter an append-only local training corpus.

Neva began as a build at the **Google DeepMind Bangalore Hackathon** and is now maintained as
a research artifact: the hackathon proved the communicative-validation loop works across
languages; the open research question is whether the same loop, run *within* a language across
dialects, yields dialect labels for free. See [`docs/RESEARCH.md`](docs/RESEARCH.md).

**Validated has two independent meanings:** meaning is validated when a partner
with a different mother tongue correctly guesses the concept; audio is validated
by automated speech-quality, contamination, and de-duplication gates.

High-throughput creative generation is load-bearing: fresh regional decks keep
play useful while **play produces the corpus. No annotator, no transcription
pass.**

![How we use Gemini and Gemma — gameplay to validated speech-concept pairs to local inference](assets/gemini-gemma-use.png)

## Hackathon tracks

Built for the [Google DeepMind Bangalore Hackathon](hackathon-details.md). We compete in:

### Primary — Problem Statement 3: High-Throughput Creative Workflows with NB2 Lite

**Focus technology:** Nano Banana 2 Lite (`gemini-3.1-flash-lite-image`)

Traditional image gen is too slow/expensive for live pipelines. NB2 Lite makes
high-volume, programmatic generation load-bearing. Dialect Data Factory uses it
as an automated regional picture-deck factory: curated concepts → generate →
verify → publish → activate for live play. Throughput, $/image, and reject rate
are first-class demo metrics—not a prompt-box-to-image toy.

Supporting stack from the event AI list: Gemini 3.5 Flash (`gemini-3.5-flash`)
for verification, speech triage, and structured game/ops calls.

### Bonus — Special Prize: Best Use of Gemma 4 (Local-First Agents on Gemma)

**Focus technology:** Gemma On-Device (Gemma 4 E2B & E4B)

Validated speech→concept pairs from the game become a same-day local corpus for
an optional QLoRA fine-tune under `tune/` (isolated from Postgres). The demo
claim is the local data loop feeding Gemma—not cloud chat with a local skin.
Tier 2 (train/compare) is cut-first if venue GPU/time does not allow.

**Primary pitch:** Track 3 pipeline velocity and unit economics. Gemma is the
bonus track when the adapter path is green.

Official schedule, rules, judging weights, and prizes: [`hackathon-details.md`](hackathon-details.md).
Living design: [`Design.md`](Design.md). Agent rules: [`AGENTS.md`](AGENTS.md).

## Architecture

- FastAPI backend, served locally and exposed through a tunnel
- Postgres 16 in Docker
- Local disk for audio, decks, and append-only corpus shards
- Independent game, deck-generation, cleaning-worker, and fine-tuning components
- Mobile player UI at `/`, venue TV at `/tv`, operator admin at `/admin`

![Dialect Data Factory high-level architecture — players, host stack, Gemini, and Gemma](assets/architecture.png)

The frozen integration contracts live in [`contracts/`](contracts/).

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

## Primary-track evidence: NB2 Lite deck factory

The primary-track path is programmatic and end-to-end: operator theme → Gemini
concepts → up to four parallel NB2 Lite image calls → Gemini verification and
retry → labels/decoys → review → explicit activation in live play.

As of 11 July 2026, four successful live runs recorded **30 accepted cards from
32 NB2 attempts**, with **2 verifier rejects**, **404.047 seconds** of summed
wall time, and **$1.0916 estimated generation cost**. Derived from those stored
run metrics:

- **4.46 accepted images/minute** across the four runs;
- **$0.0364 estimated cost/accepted image**, including retries and recorded
  Gemini Flash calls; and
- **6.25% verifier reject rate** (2/32 attempts).

The latest six-card run reached **9.85 accepted images/minute**, **$0.0342
estimated cost/accepted image**, and 0/6 rejects. Costs use the configured
pricing assumptions of $0.0336 per NB2 attempt and $0.0004 per Flash JSON call;
they are estimates, not a cloud-billing reconciliation. These are low-N local
demo observations, not benchmark or SLA claims.

Operator UI: paste the admin key at `/admin` for decks, metrics, redacted
traces, and the local Gemma training/inference demo. The Tune tab distinguishes
the live one-step training proof from the separately verified full adapter;
weak qualitative results keep inference disabled. Per-utterance stage walks
stay on the CLI:

```sh
uv run python -m scripts.pipeline_view --fixture
# or --turn-id <uuid>
```

See [`phase-plan/wave-3-launch-demo/ADMIN-DEMO-RUNBOOK.md`](phase-plan/wave-3-launch-demo/ADMIN-DEMO-RUNBOOK.md).

## Gemma training pipeline (bonus track)

The cleaning gauntlet writes **training-eligible** golden records into
`data/corpus/*.jsonl` with matching FLAC under `data/audio/`. The isolated
`tune/` harness never opens Postgres or Gemini—it only reads that local corpus.

Nothing in game code changes for a “real” run: the same three steps
(`prepare` → `train` → `compare`) swap the synthetic fixture for `data/corpus`.
With a small eligible set (for example 8 real records → ~6 train / 2 holdout),
epochs are raised so the adapter converges on the available examples. This
demonstrates the corpus→adapter loop end-to-end on authentic speech. It is not a
generalization claim.

### One-shot script

From the repo root (WSL2, GPU, model already cached offline):

```bash
chmod +x tune/run-real-demo.sh
./tune/run-real-demo.sh
```

Defaults: `TUNE_MODEL_ID=unsloth/gemma-4-E4B-it-unsloth-bnb-4bit`,
`HF_HUB_OFFLINE=1`, `TUNE_EPOCHS=40`, `TUNE_GRAD_ACCUM=2`, artifacts under
`~/gemma-runs/real-<timestamp>/`. Override with env vars (`REPO_ROOT`,
`CORPUS_DIR`, `SKIP_COMPARE=1`, etc.).

### Manual steps

```bash
export TUNE_MODEL_ID="unsloth/gemma-4-E4B-it-unsloth-bnb-4bit"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export TUNE_EPOCHS=40 TUNE_GRAD_ACCUM=2

run_root="$HOME/gemma-runs/real-$(date -u +%Y%m%dT%H%M%SZ)"
prepared="$run_root/prepared"
artifacts="$run_root/full"
mkdir -p "$run_root"

uv run --project tune python -m tune.prepare \
  --corpus "$PWD/data/corpus" \
  --data-dir "$PWD/data" \
  --output "$prepared"

uv run --project tune python -m tune.train \
  --train "$prepared/train.jsonl" \
  --dataset-manifest "$prepared/dataset_manifest.json" \
  --output "$artifacts"

uv run --project tune python -m tune.compare \
  --holdout "$prepared/holdout.jsonl" \
  --dataset-manifest "$prepared/dataset_manifest.json" \
  --adapter "$artifacts/adapter" \
  --artifact-manifest "$artifacts/artifact_manifest.json" \
  --samples 2
```

Optional live-mic beat: capture with `tune/capture_demo_audio.ps1`, then
`python -m tune.demo` with `--live-audio`, `--native-language`, and the
verified `$artifacts/adapter`. Full smoke/fixture docs: [`tune/README.md`](tune/README.md).

**Judge framing:** a handful of real rows proves corpus → adapter on authentic
speech; grow the append-only corpus and re-freeze for any generalization claim.

## Research direction

The hackathon build validates a concept when two players with **different mother tongues**
agree on it through a **shared bridge language**. That is cross-*language* validation.

The research question this repo now tracks is one step narrower and, we think, more novel:

> Run the same loop **same-language, across dialects**. If a Goalpariya speaker's clue is
> solved quickly by Goalpariya listeners and slowly by Sivasagar listeners, that
> comprehension asymmetry *is* a dialect label — and yields a perceptual distance matrix
> between varieties at no annotation cost.

Two hypotheses:

- **H1 (quality):** guess-success predicts human-rated utterance quality better than an
  ASR-confidence baseline (Whisper / MMS).
- **H2 (dialect):** the speaker-dialect × listener-dialect success matrix recovers known
  Assamese dialect groupings.

This is a **direction, not a built feature.** The current pipeline uses a shared bridge
language, so it does not yet measure dialect asymmetry. Positioning, prior work, and the
path from the hackathon artifact to a workshop paper are in
[`docs/RESEARCH.md`](docs/RESEARCH.md). Phased plan and go/no-go gates in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

## What this is not

- **Not an ASR or transcription corpus.** Records are speech→concept pairs; the
  common-language text is the system-owned deck label, not a transcript.
- **Not a generalization claim.** The demonstrated adapter is small-N and
  converged on the available examples; base-versus-tuned output is qualitative.
- **Not a large corpus today.** The demonstrated full run uses 8 eligible
  records (6 train / 2 holdout). The append-only, gated pipeline is what scales,
  not the current row count.

## Data, consent, and retention

The game captures a chosen game nickname, self-declared languages, the voice
recording, the deck concept, and the partner's game result. Accepted uploads are
stored locally as raw WebM; the worker also creates clean FLAC and quality /
validation metadata. Only training-eligible records are appended to local
`data/corpus/*.jsonl`. Inline-rejected uploads are deleted, but the current
build has no general retention deadline or post-upload deletion workflow.

The corpus and audio under `data/` are runtime-only and gitignored. This
repository publishes the **code** under AGPL-3.0; it does not automatically
publish, upload, or apply the AGPL to participant audio or corpus shards.

**Current public-venue blocker:** the UI requests browser microphone permission
and says recording begins only while the talk button is held, but it does not
yet present an explicit data-use/retention consent notice, consent checkbox, or
post-upload withdrawal/opt-out flow. Declining microphone permission or leaving
before recording prevents collection; after upload there is no player-facing
opt-out. Treat voice and language metadata as personal data: add an appropriate
notice, explicit consent, operator contact, retention period, and
withdrawal/deletion process before collecting from public participants. For an
Indian public-venue deployment, this is a DPDP-readiness blocker, not paperwork
to defer until after collection.

## Repository layout

```text
app/          FastAPI app, game core, Gemini client, admin APIs
contracts/    Frozen API, database, data-record, and directory contracts
deckgen/      Nano Banana deck-generation CLI
worker/       Async cleaning-gauntlet process
tune/         Isolated Gemma LoRA harness
frontend/     React/Vite player + TV + /admin surfaces
scripts/      Schema, deck admin, pipeline view, bootstrap helpers
build-docs/   Briefs, architecture notes, demo concept JSON
docs/         Research positioning: RESEARCH.md, ROADMAP.md
docs-assets/  README diagrams and pitch visuals
phase-plan/   Wave orchestration and runbooks
data/         Runtime-only local audio, decks, and JSONL corpus shards
```

## Scope discipline

Do not add game behavior to the frontend; it renders the server-owned state
contract. Do not change a contract without coordinating both backend and
frontend owners. Keep Gemini model IDs in `app/models.py` and prompts in named
modules (`deckgen/prompts.py`, `worker/prompts.py`).

## Acknowledgements

Prototyped at the Google DeepMind Bangalore Hackathon. Neva is not affiliated with or endorsed
by Google or Google DeepMind; the hackathon is where the loop was first built and tested.

## License

This project's code is licensed under the
[GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0). Runtime audio,
corpus data, third-party model weights, and generated adapters are not relicensed
by that statement.

The exact `google/gemma-4-E4B-it` base model and the
`unsloth/gemma-4-E4B-it-unsloth-bnb-4bit` checkpoint both declare
[Apache License 2.0](https://ai.google.dev/gemma/docs/gemma_4_license). Google's
[Gemma Terms of Use](https://ai.google.dev/gemma/terms) explicitly direct Gemma
4 users to that separate license; the custom terms listed there cover earlier
Gemma families, not Gemma 4. Redistribution of Gemma 4 weights or a derived
adapter must still satisfy Apache 2.0's license, notice, attribution, and
modified-file requirements.
