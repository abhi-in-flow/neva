# Admin + Tuning Demo Runbook

Multi-surface judging demo that pairs the existing game/`/tv` close with the
new `/admin` operator UI and an optional terminal Tier 2 beat.

## Surfaces

| Surface | URL / command | Shows |
|---------|---------------|-------|
| Player phones | `/` | Live round (image-before-label) |
| Operator admin | `/admin` | Decks, Metrics, Traces, Tune runbook |
| Venue TV | `/tv` | Leaderboard + Tier 1 ticker |
| Pipeline CLI | `uv run python -m scripts.pipeline_view --fixture` | Sanitized stage walk |
| Tier 2 | `uv run python -m tune.demo ...` | Preflight, compare, optional mic |

## Three-minute order

1. `/admin` → Decks: generate or activate a regional deck; show generation metrics.
2. Two phones: one speaker/guesser round (emphasize image before label).
3. `/admin` → Metrics + Traces: funnel counts, worker healthy, recent
   `gauntlet_triage` rows (prompts redacted).
4. Optional: `scripts.pipeline_view --turn-id <uuid>` for one sanitized stage walk.
5. `/tv` Tier 1 close: pairs, languages, cost/sample, pass rate.
6. Terminal Tier 2 **only if SHOW** (below).

## Privacy / redaction

- Admin traces return model, operation, status, latency, cost, and token/error
  metadata. Prompt text is length-only / redacted.
- Never open raw audio on the projector from `/admin`.
- Pipeline CLI shows paths and gate booleans, not nicknames or prompt text.

## Tier 2 SHOW / CUT

**SHOW** when all are true:

1. `uv run python -m tune.preflight` exits 0.
2. Verified `--full-adapter` has a compatible `artifact_manifest.json`.
3. Dry rehearsal of `tune.demo --dry-run` succeeded earlier.
4. Compare output on a held-out sample looks coherent (no gibberish claim).

**CUT** (do not delay submission) when any fail:

- GPU / HF / Unsloth preflight failure
- Missing or incompatible adapter
- Live mic capture fails (use disclosed `--fallback-audio` or skip stage 5)
- Short live QLoRA smoke fails (continue with verified adapter; do not claim
  same-day train succeeded)

## Operator commands

```bash
# Admin key must match DECK_ADMIN_API_KEY
# Open https://<host>/admin and paste the key once

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
