# Isolated Gemma 4 LoRA harness

This package reads only golden JSONL shards and clean FLAC paths. It never
imports the app or worker, accesses Postgres, calls Gemini, or writes into the
source corpus.

## Shipped paths

- **Primary: audio-first scaffolding.** `prepare --mode audio` emits
  conversational rows whose user content contains task text plus
  `{"type": "audio", "audio": "<absolute FLAC path>"}`. The assistant target is
  the system-owned `common_lang_text`.
- **Explicit fallback: text sidecar.** `prepare --mode text --transcripts
  transcripts.jsonl` requires one caller-supplied
  `{"utterance_id": "...", "transcript": "..."}` row per eligible record. The
  transcript is never inferred from golden metadata and the golden corpus is
  never changed. Producing real transcripts is an external preprocessing
  responsibility; this isolated harness does not call Gemini.

No mode switches automatically. Audio incompatibility must be reported before
choosing the text fallback.

## Dependency-light verification

```powershell
$tmp = Join-Path $env:TEMP "dialect-lora-fixture"
uv run python -m tune.make_dummy --output $tmp
uv run python -m tune.prepare --corpus "$tmp\corpus" --data-dir $tmp --output "$tmp\prepared"
uv run python -m tune.train --train "$tmp\prepared\train.jsonl" --dry-run
uv run python -m tune.compare --holdout "$tmp\prepared\holdout.jsonl" --dry-run
```

The dummy command refuses a non-empty output directory. Its `fLaC` files are
validation stubs, not playable audio, and prove preparation only.

## Optional real training stack

Use Python 3.12 in WSL2/Linux unless the selected package releases explicitly
support native Windows:

- NVIDIA driver and CUDA version supported by the selected PyTorch wheel
- `torch`
- `unsloth`
- `transformers`
- `trl`
- `peft`
- `accelerate`
- `bitsandbytes`
- `datasets`

Versions must be selected together from current Unsloth Gemma 4 guidance.
Audio SFT is deliberately treated as unverified: the installed Gemma 4
processor and Unsloth/TRL versions must accept local FLAC conversational content
items. Real training errors rather than claiming success or silently changing
to text.

```powershell
uv run python -m tune.train --train <prepared>\train.jsonl --output <run>
uv run python -m tune.compare --holdout <prepared>\holdout.jsonl `
  --adapter <run>\adapter --predictions <private>\predictions.jsonl
uv run python -m tune.metrics --predictions <private>\predictions.jsonl `
  --output <private>\metrics.json
```

Configuration is centralized in `tune/config.py` and may be overridden with
`TUNE_*` environment variables. The defaults are E4B 4-bit QLoRA, rank 16,
dropout 0, batch 1, gradient accumulation 8, three epochs, and deterministic
seed `20260711`. The 20 percent holdout uses native-language strata and
largest-remainder allocation, then SHA-256 ordering by seed and utterance ID.

