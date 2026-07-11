#!/usr/bin/env bash
# Chain prepare → train → compare against the live append-only corpus.
#
# Demo-tuned for a small eligible set: more epochs / lower grad-accum so the
# adapter visibly diverges from base on held-out real speech. Does not mutate
# data/corpus or touch Postgres. Artifacts land under ~/gemma-runs/real-*.
#
# Usage (from repo root):
#   ./tune/run-real-demo.sh
#   REPO_ROOT=/path/to/neva ./tune/run-real-demo.sh
#   SKIP_COMPARE=1 ./tune/run-real-demo.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${REPO_ROOT:-$SCRIPT_DIR/..}" && pwd)"
CORPUS_DIR="${CORPUS_DIR:-$REPO_ROOT/data/corpus}"
DATA_DIR="${DATA_DIR:-$REPO_ROOT/data}"

export TUNE_MODEL_ID="${TUNE_MODEL_ID:-unsloth/gemma-4-E4B-it-unsloth-bnb-4bit}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
# Small real corpora need more passes than the stock 3-epoch profile.
export TUNE_EPOCHS="${TUNE_EPOCHS:-40}"
export TUNE_GRAD_ACCUM="${TUNE_GRAD_ACCUM:-2}"

if [[ ! -d "$CORPUS_DIR" ]]; then
  echo "error: corpus directory missing: $CORPUS_DIR" >&2
  exit 1
fi
if ! compgen -G "$CORPUS_DIR/*.jsonl" >/dev/null; then
  echo "error: no JSONL shards under $CORPUS_DIR" >&2
  exit 1
fi

run_root="${RUN_ROOT:-$HOME/gemma-runs/real-$(date -u +%Y%m%dT%H%M%SZ)}"
prepared="$run_root/prepared"
artifacts="$run_root/full"
mkdir -p "$run_root"

echo "==> prepare corpus=$CORPUS_DIR output=$prepared"
uv run --project "$SCRIPT_DIR" python -m tune.prepare \
  --corpus "$CORPUS_DIR" \
  --data-dir "$DATA_DIR" \
  --output "$prepared"

echo "==> train epochs=$TUNE_EPOCHS grad_accum=$TUNE_GRAD_ACCUM output=$artifacts"
uv run --project "$SCRIPT_DIR" python -m tune.train \
  --train "$prepared/train.jsonl" \
  --dataset-manifest "$prepared/dataset_manifest.json" \
  --output "$artifacts"

if [[ "${SKIP_COMPARE:-0}" != "1" ]]; then
  echo "==> compare holdout vs adapter"
  uv run --project "$SCRIPT_DIR" python -m tune.compare \
    --holdout "$prepared/holdout.jsonl" \
    --dataset-manifest "$prepared/dataset_manifest.json" \
    --adapter "$artifacts/adapter" \
    --artifact-manifest "$artifacts/artifact_manifest.json" \
    --samples "${COMPARE_SAMPLES:-2}"
fi

echo
echo "Done. Artifacts: $artifacts"
echo "Prepared split:  $prepared"
echo "Optional live mic: capture with tune/capture_demo_audio.ps1, then:"
echo "  uv run --project tune python -m tune.demo \\"
echo "    --prepared \"$prepared\" \\"
echo "    --full-adapter \"$artifacts/adapter\" \\"
echo "    --full-artifact-manifest \"$artifacts/artifact_manifest.json\" \\"
echo "    --live-audio <wsl-path-to-flac> \\"
echo "    --native-language <tag>"
