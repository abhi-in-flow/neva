# LoRA Harness — Coding Agent

## Goal

Build an isolated, optional Gemma tuning pipeline that consumes only golden
corpus shards and clean FLAC files.

## Owned paths

`tune/` and `tests/tune/`. Do not import application or worker code and never
access Postgres.

## Required behavior

- Prepare eligible examples with a 20% language-stratified holdout.
- Attempt Gemma audio-input SFT first; implement the documented text fallback
  if the library path is unavailable.
- Train E4B QLoRA with centralized parameters sized for the RTX 5090.
- Compare base versus tuned output on five held-out samples.
- Write private evaluation metrics to JSON without making headline claims.
- Generate a 100-row dummy corpus in an isolated temporary directory.
- Provide dry-run/config validation that does not download models or mutate
  real corpus data.

## Constraints

Follow `AGENTS.md`. Treat model quality as optional theatre, never the primary
claim. Do not edit app, worker, deck, frontend, or contracts. Report dependency
or Windows/GPU incompatibility immediately; do not hide a fallback.

## Verification and handoff

Run preparation and dry-run tests against dummy data. If full training is
possible, record duration and VRAM; otherwise identify the exact blocker and
validated fallback. Report files, commands, results, and reproducibility notes.
