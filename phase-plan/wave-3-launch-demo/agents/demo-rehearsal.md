# Demo Rehearsal — Exploration Agent

## Goal

Stress-test the three-minute live-demo sequence and define immediate fallbacks
without changing product code.

## Rehearse

Time each beat: deck pipeline, live game round, gauntlet record, metrics close,
and optional tuning comparison. Test the sequence once normally and once with
Gemini unavailable, tunnel latency, an empty matchmaking queue, and Tier 2 cut.

## Constraints

Read-only. Do not edit the frontend/backend, fabricate metrics, expose
participant data, or add new features. Prefer a shorter reliable sequence over
more technical surface area.

## Deliverable

Return:

- timestamped run-of-show under three minutes;
- operator/device responsibilities;
- trigger and fallback for each failure mode;
- assets that must be preloaded without misrepresenting them as live;
- explicit `SHOW` or `CUT` decision for Tier 2.
