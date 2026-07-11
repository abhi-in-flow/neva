# Deck Engine — Coding Agent

## Goal

Build a standalone regional picture-deck CLI whose high-throughput generation,
verification, rejection, cost, and publication are visible and measurable.

## Owned paths

`deckgen/` and `tests/deckgen/`. Contracts and shared app files are read-only.

## Required behavior

- CLI shape: `python -m deckgen --region <region> --cards <count>`.
- Curated culturally safe concepts with multilingual system labels.
- Nano Banana 2 Lite generation using the provided prompt template.
- Gemini image-label verification and bounded regeneration.
- Batched label translation and decoy selection from the provided pool only.
- Resolve selected concept IDs to card IDs and store `cards.decoys` as a JSON
  array of same-deck card UUID strings.
- Atomic database insert followed by `decks.status = 'live'`.
- Metrics for images/minute, cost/image, reject rate, and total deck cost.
- Dry-run mode with fake images/responses and no DB or runtime-data mutation.

## Constraints

Follow `AGENTS.md`. Put prompts in `deckgen/prompts.py`, concepts in their own
module, and request shared config/client changes from the orchestrator. Strip
inline image bytes from logs.

## Verification and handoff

Test generation retries, verification rejection, decoy validity, metrics, and
atomic publication with fakes. Run focused tests and lint. Report files,
commands, expected live cost, results, and quota assumptions.
