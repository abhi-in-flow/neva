# Contract Auditor — Exploration Agent

## Goal

Find contradictions that would prevent isolated Wave 1 work. This is a
read-only assignment.

## Read

Read `AGENTS.md`, `Design.md`, the three `build-docs/` files in their stated
order, and every file under `contracts/`. Inspect `app/` only to compare the
scaffold against those contracts.

## Check

- schema fields support the API and golden record;
- enum/state names match the frontend handoff exactly;
- label and audio visibility cannot leak across phases;
- job claiming and corpus ownership support multiple workers safely;
- model names, paths, metrics, and API-call logging requirements have one
  canonical location;
- every Wave 1 component can work without editing another component's paths.

## Constraints

Do not edit files, run mutating commands, inspect `.env`, or propose optional
features. Separate critical contract blockers from non-blocking improvements.

## Deliverable

Return findings ordered by severity with exact file/section references, the
affected Wave 1 owners, and the smallest safe resolution. Explicitly state
`NO CRITICAL MISMATCHES` if none exist.
