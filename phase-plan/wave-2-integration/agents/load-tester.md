# Load Tester — Coding Agent

## Goal

Measure the polling-heavy venue workload and find safe API/DB concurrency
settings without changing live data.

## Owned paths

`tools/load/`, `tests/load/`, and load-specific documentation. Production
component code and contracts are read-only.

## Workload

- 200 fake authenticated clients polling `/api/state` every two seconds;
- controlled join/pair/action traffic against seeded isolated data;
- optional bounded fixture uploads, never real audio;
- delayed/failing fake Gemini worker behavior while gameplay continues.

## Measurements

Capture request rate, p50/p95/p99 latency, error rate, DB pool saturation,
worker backlog, CPU/RAM, and recovery after load stops. Centralize configurable
client counts, duration, and rates.

## Constraints

Provide a dry-run/config-check mode. Require an explicit non-production target
marker before generating traffic. Do not tune source code; report evidence and
recommended settings to the orchestrator.

## Handoff

Return commands, test target safeguards, measurements, bottlenecks, safe
concurrency recommendation, and exact reproduction steps.
