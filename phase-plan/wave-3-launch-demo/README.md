# Wave 3 — Launch and Demo

## Scope

Launch the venue pilot, preserve operational stability, and assemble a
defensible submission and three-minute demonstration from measured evidence.

## Parallel workstreams

- **Launch monitor** — operational monitoring and incident triage.
- **Evidence verifier** — read-only validation of headline metrics and claims.
- **Demo rehearsal** — read-only reliability review of the live demo sequence.
- **Submission packager** — owns submission-facing documentation/assets only.

The orchestrator controls production changes. Monitoring agents do not
autonomously mutate live services or data.

## Dependency graph

```text
Wave 2 release
  ├── launch monitor ─────┐
  ├── evidence verifier ──┼── orchestrator go/no-go and cuts
  ├── demo rehearsal ─────┤                 │
  └── submission package ─┘                 ▼
                                  public repo + video + demo
```

## Acceptance gate

- Public URL and QR work from a non-venue-network phone.
- At least one complete live round succeeds after launch.
- Headline counts and costs are traceable and unit-correct.
- Demo has a tested offline/degraded fallback for every external dependency.
- README clearly states what was built during the hackathon.
- Public repository contains no secrets, participant data, or prohibited
  runtime assets.
- Submission links and one-minute video are accessible.

## Handoff checklist

- [ ] Freeze demo build and record commit/reference.
- [ ] Record final Tier 1 metrics and timestamp.
- [ ] Decide whether Tier 2 model comparison is safe to show.
- [ ] Complete repository/privacy/secret scan.
- [ ] Confirm all team members and accessible links.
- [ ] Mark decision: `PASS` / `PASS WITH CUTS` / `BLOCKED`.
