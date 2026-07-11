# Wave 0 Handoff

## Decision

`PASS WITH CUTS` — approved to begin Wave 1.

## Accepted foundation

- Python 3.12 environment is locked with `uv`.
- Postgres 16 is healthy; canonical schema and forward migration are applied.
- FastAPI health and static root pass locally.
- Phase 0 smoke suite and lint pass.
- The `triage → package` contract resolves asynchronous quality versus human
  validation timing, and the active database matches it.
- ffmpeg with FLAC support and the Cloudflare connector service are installed.
- Wave 1 path ownership and self-contained briefs are frozen.

## Deferred cut

The named Cloudflare tunnel is connected but has no published application
route. Public hostname routing and phone HTTPS verification are explicitly
deferred until after Wave 1. Before launch, add a route from the chosen hostname
to `http://localhost:8000` and verify `/` plus `/api/health` from a phone.

## Contract revision notice

All Wave 1 owners must use the revised `contracts/schema.sql`,
`contracts/golden_record.md`, and `contracts/api_types.py`. Existing databases
must run `uv run python -m scripts.apply_migrations`.
