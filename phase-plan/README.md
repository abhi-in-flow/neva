# Delivery Waves

This directory converts the architecture phases into independently executable
waves. Each wave has one orchestrator that owns sequencing, contract
arbitration, integration, and acceptance. The orchestrator delegates isolated
work to coding or exploration subagents and does not duplicate their work.

## Wave map

| Wave | Included phases | Outcome |
|---|---|---|
| [Wave 0 — Foundation](wave-0-foundation/README.md) | Phase 0 | Frozen contracts and a verified WSL2/Linux runtime |
| [Wave 1 — Parallel Build](wave-1-parallel-build/README.md) | Phases 1–5 | Game, gauntlet, deck engine, tuning harness, and frontend built in parallel |
| [Wave 2 — Integration](wave-2-integration/README.md) | Phase 6 | Components integrated, load-tested, and operationally recoverable |
| [Wave 3 — Launch and Demo](wave-3-launch-demo/README.md) | Launch, evidence, submission | Public pilot and defensible judging package |

## Execution rules

1. A wave starts only after the previous wave's acceptance gate passes.
2. One orchestrator owns each wave. It delegates implementation and
   exploration, resolves contract questions, and performs final integration.
3. Every subagent has exclusive write ownership of listed paths. Shared
   contracts are read-only unless the orchestrator explicitly reassigns them.
4. Parallelize tasks only when their write sets do not overlap and their inputs
   are frozen.
5. Each subagent brief is self-contained: goal, context, owned paths,
   constraints, deliverables, verification, and handoff format.
6. Exploration agents are read-only. Coding agents must run their own focused
   checks before handoff.
7. A failed contract assumption is reported to the orchestrator; subagents do
   not silently patch around it.
8. All repository-wide rules in `AGENTS.md` apply to every wave.

## Shared handoff format

Every subagent reports:

- outcome and files changed;
- commands run and their results;
- acceptance criteria met or missed;
- assumptions and contract questions;
- known risks, remaining work, and the safest rollback;
- no secrets, raw audio, images, or inline GenAI payloads.

The orchestrator records the wave decision as `PASS`, `PASS WITH CUTS`, or
`BLOCKED` in that wave's handoff checklist before advancing.
