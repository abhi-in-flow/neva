# Admin + Tuning Demo Runbook

Multi-surface judging demo that pairs the existing game/`/tv` close with the
new `/admin` operator UI and an optional terminal Tier 2 beat.

## Surfaces

| Surface | URL / command | Shows |
|---------|---------------|-------|
| Player phones | `/` | Live round (image-before-label) |
| Operator admin | `/admin` | Decks, Metrics, Traces, Tune progress/inference |
| Venue TV | `/tv` | Leaderboard + Tier 1 ticker |
| Pipeline CLI | `uv run python -m scripts.pipeline_view --fixture` | Sanitized stage walk |
| Tier 2 | `/admin` → Tune | Live one-step proof, approved compare, optional mic |

## Three-minute order

1. `/admin` → Decks: generate or activate a regional deck; show generation metrics.
2. Two phones: one speaker/guesser round (emphasize image before label).
3. `/admin` → Metrics + Traces: funnel counts, worker healthy, recent
   `gauntlet_triage` rows (prompts redacted).
4. Optional: `scripts.pipeline_view --turn-id <uuid>` for one sanitized stage walk.
5. `/tv` Tier 1 close: pairs, languages, cost/sample, pass rate.
6. `/admin` → Tune Tier 2 **only if SHOW** (below): distinguish the live
   one-step proof from the separately verified adapter used for inference.

## Privacy / redaction

- Admin traces return model, operation, status, latency, cost, and token/error
  metadata. Prompt text is length-only / redacted.
- Held-out audio remains admin-authenticated. Do not play it on the projector
  without participant/demo-fixture clearance.
- Tune-tab microphone audio is temporary, is deleted after the configured
  retention window, and never enters the training corpus.
- Pipeline CLI shows paths and gate booleans, not nicknames or prompt text.

## Tier 2 SHOW / CUT

**SHOW** when all are true:

1. `uv run python -m tune.preflight` exits 0.
2. Verified `--full-adapter` has a compatible `artifact_manifest.json`.
3. Dry rehearsal of `tune.demo --dry-run` succeeded earlier.
4. Compare output on a held-out sample looks coherent (no gibberish claim).
5. The Tune overview reports the comparison set as operator-approved.

**CUT** (do not delay submission) when any fail:

- GPU / HF / Unsloth preflight failure
- Missing or incompatible adapter
- Live mic capture fails (use disclosed `--fallback-audio` or skip stage 5)
- Short live QLoRA smoke fails (continue with verified adapter; do not claim
  same-day train succeeded)
- Full adapter exists but qualitative outputs are weak (show training proof
  only; keep tuned inference gated)

## Operator commands

```bash
# Admin key must match DECK_ADMIN_API_KEY
# Open https://<host>/admin and paste the key once

# Host-only tune supervisor (separate terminal, no Docker/GPU imports in API)
export TUNE_MODEL_ID="<exact model_id from the approved artifact manifest>"
export TUNE_DEMO_PREPARED_DIR="<prepared_dir>"
export TUNE_DEMO_FULL_ADAPTER="<verified_full_adapter>"
export TUNE_DEMO_ARTIFACT_MANIFEST="<verified_artifact_manifest.json>"
export TUNE_DEMO_APPROVED_PREDICTIONS="<reviewed_predictions.jsonl>"
export TUNE_DEMO_APPROVED_SAMPLE_IDS="<comma-separated approved holdout IDs>"
uv run python -m scripts.tune_demo_supervisor --dry-run
uv run python -m scripts.tune_demo_supervisor

# Fixture pipeline walk (safe)
uv run python -m scripts.pipeline_view --fixture

# Live pipeline walk
uv run python -m scripts.pipeline_view --turn-id <uuid>

# Tier 2 rehearsal
uv run python -m tune.demo \
  --prepared <prepared_dir> \
  --live-run-output /tmp/neva-live-run \
  --full-adapter <verified/adapter> \
  --dry-run
```

## Fallbacks

| Failure | Action |
|---------|--------|
| Gemini slow | Keep gameplay; show backlog on Traces jobs; drain later |
| Empty metrics | Close on deck throughput + honest low-n Tier 1 |
| Admin 503 | Key unset — use `scripts.deck_admin` CLI instead |
| Tier 2 weak | CUT; Tier 1 still wins the pitch |
