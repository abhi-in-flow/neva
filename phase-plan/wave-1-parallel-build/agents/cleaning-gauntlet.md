# Cleaning Gauntlet — Coding Agent

## Goal

Build a standalone, restart-safe worker that converts validated turns into
quality-gated golden records and append-only JSONL shards.

## Owned paths

`worker/` and `tests/gauntlet/`. Read contracts and shared app infrastructure;
do not import FastAPI or edit `app/`.

## Required behavior

- Claim pending jobs transactionally with `FOR UPDATE SKIP LOCKED`.
- For `triage`: transcode to 16 kHz mono FLAC, make one structured Gemini
  quality/contamination call, compute the per-speaker duplicate fingerprint,
  and store machine quality on the turn.
- After triage, idempotently enqueue `package` only when the turn is scored.
- For `package`: recompute eligibility exactly from the scored human result and
  stored quality, write the canonical record, append eligible records to the
  current shard, rotate at the configured limit, and bump metrics.
- Retry with exponential backoff and park poison jobs after the configured
  maximum.
- Provide fixture/dry-run mode that never changes runtime data.

## Constraints

Follow `AGENTS.md`, including safe GenAI request/output logs with inline audio
stripped. Prompts belong in `worker/prompts.py`. Request shared-client/config
changes from the orchestrator.

## Verification and handoff

Test clean, silent, and label-readout fixtures using fake GenAI responses and
isolated paths. Run focused tests and lint. Report schema/client assumptions,
files, commands, results, and failure recovery.
