# Wave 3 Orchestrator Runbook

## Mission

Keep the live pilot stable while turning verified system behavior into a clear,
honest, and repeatable judging demonstration.

## Startup

1. Accept the Wave 2 release and documented limits.
2. Freeze feature work; permit only critical launch fixes.
3. Snapshot counts and back up the database/runtime data.
4. Launch all four subagents concurrently using `agents/`.
5. Assign one human operator for API/DB/worker processes and one for gameplay.

## Incident policy

- Protect gameplay and collected data before optional demo features.
- If Gemini slows, leave gameplay running and allow backlog to drain.
- If tuning fails or looks weak, cut Tier 2 without delaying submission.
- If a critical fix is required, reproduce it, make the smallest change, rerun
  its focused check, and record it.
- Do not let agents restart services, mutate DNS, or change production data
  without orchestrator approval.

## Demo order

1. Show the generated regional deck and throughput instrumentation.
2. Run one speaker/guesser round emphasizing image-before-label.
3. Show the validated record moving through the zero-touch gauntlet.
4. Close on live Tier 1 counts, languages, time, and cost/sample.
5. Show base-versus-tuned output only if the pre-agreed go/no-go passes.

## Completion

Use evidence and rehearsal reports to cut unreliable claims, complete the
submission checklist, verify public access, update `Design.md` status, and
publish the final go/no-go report.
