# Isolated Gemma 4 E4B audio QLoRA

This WSL2-only environment reads golden JSONL and clean FLAC files. It never
imports the backend or worker, accesses Postgres, calls Gemini, mutates the
source corpus, or depends on the frontend.

## Environment

The ML stack is isolated in `tune/.venv` and locked by `tune/uv.lock`. Keep
large extraction and model caches on the native WSL filesystem:

```bash
export UV_CACHE_DIR="$HOME/.cache/uv"
export TMPDIR="$HOME/.cache/tmp"
export HF_HOME="$HOME/.cache/huggingface"
mkdir -p "$UV_CACHE_DIR" "$TMPDIR" "$HF_HOME"

uv sync --project tune --python 3.12
uv run --project tune python -m tune.preflight
```

The preflight requires WSL2, Python 3.12, CUDA Torch with bf16, an RTX 5090
with at least 17 GiB free VRAM, ffmpeg/ffprobe, Hugging Face authentication
(or explicit offline mode with a cached checkpoint), access to
`unsloth/gemma-4-E4B-it-unsloth-bnb-4bit`, and at least 40 GiB free cache
space.

## Model license

The exact Unsloth checkpoint and its `google/gemma-4-E4B-it` base model declare
[Apache License 2.0](https://ai.google.dev/gemma/docs/gemma_4_license). Google's
[Gemma Terms of Use](https://ai.google.dev/gemma/terms) explicitly send Gemma 4
users to that separate license; their appendix covers earlier Gemma families.
The repository's MIT license covers project code and does not relicense
Gemma weights, generated adapters, participant audio, or corpus data. Anyone
redistributing weights or an adapter must preserve the Apache 2.0 license,
notices, attribution, and modification notices.

Transformers is deliberately overridden to `>=5.10.0`: older Gemma 4 audio
processors can mismatch expanded audio tokens and encoder features. Training
also verifies the first collated batch contains `input_features` and
`input_features_mask`; it fails before the optimizer if audio was dropped.

## Dependency-light verification

Run repository tests in the main API environment:

```bash
uv run pytest -q tests/tune
uv run ruff check tune tests/tune
```

The ordinary dummy fixture contains FLAC signature stubs and is preparation
only. The GPU smoke fixture creates real, decodable synthetic tones outside the
runtime corpus:

```bash
fixture="$HOME/.cache/neva-gemma-smoke-fixture"
prepared="$HOME/.cache/neva-gemma-smoke-prepared"

uv run --project tune python -m tune.make_smoke_fixture --output "$fixture"
uv run --project tune python -m tune.prepare \
  --corpus "$fixture/corpus" \
  --data-dir "$fixture" \
  --output "$prepared"
```

Preparation recomputes every eligibility gate, rejects escaping/missing FLAC
paths, creates the deterministic language-stratified 80/20 split, and writes
`dataset_manifest.json`. Audio appears before instruction text in each user
turn; assistant output is typed text content.

## One-step GPU smoke

Use a new output directory. This performs exactly one optimizer step and may
not be used as evidence of model quality:

```bash
uv run --project tune python -m tune.train \
  --train "$prepared/train.jsonl" \
  --dataset-manifest "$prepared/dataset_manifest.json" \
  --output "$HOME/.cache/neva-gemma-smoke-run" \
  --max-steps 1
```

Success requires `adapter/`, `training_metrics.json`, and
`artifact_manifest.json`. The metrics record elapsed time and peak allocated
VRAM. The artifact manifest binds the adapter hash to the frozen corpus hash,
model, split seed, language/sample counts, and LoRA configuration.

Run one adapter inference only to prove artifact loading; synthetic tones do
not support a quality claim:

```bash
uv run --project tune python -m tune.compare \
  --holdout "$prepared/holdout.jsonl" \
  --dataset-manifest "$prepared/dataset_manifest.json" \
  --adapter "$HOME/.cache/neva-gemma-smoke-run/adapter" \
  --artifact-manifest "$HOME/.cache/neva-gemma-smoke-run/artifact_manifest.json" \
  --samples 1
```

## Full eligible-corpus run

This remains an explicit go/no-go operation. Freeze the corpus before
preparation and choose artifact paths outside `data/`. The same
`prepare` → `train` → `compare` path used for synthetic fixtures points at
`data/corpus` and `data/` for real human speech (audio mode).

### Real-corpus demo (small eligible set)

When only a handful of records are training-eligible (for example 8 real →
~6 train / 2 holdout), the stock 3-epoch / grad-accum-8 profile finishes in too
few optimizer steps to converge on the available examples. The demo profile
raises epochs to demonstrate the corpus→adapter loop end-to-end on authentic
speech. It is not a generalization claim.

One-shot (repo root):

```bash
./tune/run-real-demo.sh
```

Or manually:

```bash
export TUNE_MODEL_ID="unsloth/gemma-4-E4B-it-unsloth-bnb-4bit"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export TUNE_EPOCHS=40
export TUNE_GRAD_ACCUM=2

run_root="$HOME/gemma-runs/real-$(date -u +%Y%m%dT%H%M%SZ)"
prepared="$run_root/prepared"
artifacts="$run_root/full"
mkdir -p "$run_root"

uv run --project tune python -m tune.prepare \
  --corpus "/home/abhilash/neva/data/corpus" \
  --data-dir "/home/abhilash/neva/data" \
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

The default full profile (without the demo overrides) uses E4B instruction-tuned
4-bit QLoRA, rank 16, dropout 0, batch 1, gradient accumulation 8, cosine
scheduling, and three epochs. All controls are centralized in `tune/config.py`
and use `TUNE_*` overrides. Re-freeze and re-run as the append-only corpus grows.

```bash
run_root="$HOME/gemma-runs/$(date -u +%Y%m%dT%H%M%SZ)"
prepared="$run_root/prepared"
artifacts="$run_root/full"
mkdir -p "$run_root"

uv run --project tune python -m tune.prepare \
  --corpus "/home/abhilash/neva/data/corpus" \
  --data-dir "/home/abhilash/neva/data" \
  --output "$prepared"

uv run --project tune python -m tune.train \
  --train "$prepared/train.jsonl" \
  --dataset-manifest "$prepared/dataset_manifest.json" \
  --output "$artifacts"
```

Resume only from a checkpoint beneath the same output directory:

```bash
uv run --project tune python -m tune.train \
  --train "$prepared/train.jsonl" \
  --dataset-manifest "$prepared/dataset_manifest.json" \
  --output "$artifacts" \
  --resume-from-checkpoint "$artifacts/checkpoints/checkpoint-N"
```

## Base versus tuned evaluation

The comparison command validates the full adapter against the frozen dataset,
loads pristine base and adapter models separately, and evaluates exactly five
deterministic holdout rows:

```bash
uv run --project tune python -m tune.compare \
  --holdout "$prepared/holdout.jsonl" \
  --dataset-manifest "$prepared/dataset_manifest.json" \
  --adapter "$artifacts/adapter" \
  --artifact-manifest "$artifacts/artifact_manifest.json" \
  --predictions "$run_root/private/predictions.jsonl"

uv run python -m tune.metrics \
  --predictions "$run_root/private/predictions.jsonl" \
  --output "$run_root/private/metrics.json"
```

Aggregate metrics remain private unless the result is clearly favorable.

## Hybrid stage demo

First rehearse without GPU or model work:

```bash
uv run --project tune python -m tune.demo \
  --prepared "$prepared" \
  --live-run-output "$run_root/stage-smoke" \
  --full-adapter "$artifacts/adapter" \
  --full-artifact-manifest "$artifacts/artifact_manifest.json" \
  --dry-run
```

Capture temporary microphone audio on Windows without touching the corpus:

```powershell
.\tune\capture_demo_audio.ps1 `
  -DeviceName "Microphone (your device name)" `
  -OutputPath "$env:TEMP\gemma-live.flac"
```

Then pass its WSL-visible path plus a validated fallback recording:

```bash
uv run --project tune python -m tune.demo \
  --prepared "$prepared" \
  --live-run-output "$run_root/stage-smoke" \
  --full-adapter "$artifacts/adapter" \
  --full-artifact-manifest "$artifacts/artifact_manifest.json" \
  --live-audio "/mnt/c/Users/<user>/AppData/Local/Temp/gemma-live.flac" \
  --fallback-audio "$prepared_demo_recording" \
  --native-language "Assamese"
```

The stage run labels the short adapter separately, then switches explicitly to
the pre-completed compatible adapter. Short-training failure is shown and the
verified adapter continues. Failed microphone capture uses the supplied
validated fallback; neither recording enters the corpus.

## Protected web demo bridge

The Docker API never imports this environment. Start the single host-GPU
supervisor from the repository root and let `/admin` exchange only bounded
files under `data/tune-demo`:

```bash
export TUNE_MODEL_ID="unsloth/gemma-4-E4B-it-unsloth-bnb-4bit"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export TUNE_DEMO_PREPARED_DIR="$prepared"
export TUNE_DEMO_FULL_ADAPTER="$artifacts/adapter"
export TUNE_DEMO_ARTIFACT_MANIFEST="$artifacts/artifact_manifest.json"
export TUNE_DEMO_APPROVED_PREDICTIONS="$run_root/private/predictions.jsonl"
# Comma-separated holdout IDs manually approved after qualitative review.
export TUNE_DEMO_APPROVED_SAMPLE_IDS="<approved-utterance-id>"

uv run python -m scripts.tune_demo_supervisor --dry-run
uv run python -m scripts.tune_demo_supervisor
```

The supervisor accepts only one-step smoke training and temporary live-audio
inference. Full training remains an operator CLI action. A technically valid
full artifact stays visible in the UI, but inference remains disabled until at
least one matching held-out comparison is explicitly approved. Microphone
uploads must be 1–8 seconds and are removed after inference or the configured
TTL; they never enter `train.jsonl` or the append-only corpus.

