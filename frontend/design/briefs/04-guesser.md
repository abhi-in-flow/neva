# Design Brief — Guesser Screen

Status: **confirmed** (impeccable shape, 2026-07-11)

## 1. Feature Summary
The other half of the game: B hears their partner describe a card in a language B *doesn't* natively speak, and picks what it meant from 4–6 options. B's correct guess is the human validation gate that makes a recording training-eligible — so the design's job is to make genuinely listening the path of least resistance.

## 2. Primary User Action
Tap play, actually listen, then commit to one fat colored tile.

## 3. Design Direction
Full palette moment — the screen the answer-tile color family exists for. Peacock world; the play hero mirrors the speaker's record button (same scale, same family — the game's two sides visibly rhyme); tiles land in distinct family colors per the Never-Alone Rule (color + position + full-size text, never color alone). Scene sentence: leaning in, phone speaker pressed to your ear against the hall noise, hearing a stranger's grandmother-language and hunting for the meaning — the screen must reward the listening, then make the choice feel like slamming a buzzer. Anchors: Kahoot's answer-tile slam, WhatsApp voice-note familiarity (replay affordance), HQ Trivia's lock-in suspense.

## 4. Scope
Production-ready; this one phase screen; shipped-quality audio playback + guess flow; mock fixtures including `attempts_left: 1` and an unplayable-audio case.

## 5. Layout Strategy
- **Pre-listen:** top half is the partner-framed hero — "{partner} describes…" above a huge circular play button (progress ring during playback); bottom half intentionally empty with a quiet "listen first" line.
- **Post-listen:** tiles stagger in below (the reveal is the beat that says "now you may answer"); hero collapses to a compact replay chip pinned above the grid.
- Tile grid: 2-column for short labels, automatically falling to stacked full-width rows when any label is long (Indic scripts decide, not the design) — every tile ≥64px tall, label in full-size Noto ≥24px.

## 6. Key States
- Pre-listen (play hero only, tiles absent)
- Playing (progress ring; no pause — clips ≤8s)
- Listened → tiles reveal (staggered entrance, settles per The Settle Rule); replay chip available
- **Guess locked in:** picked tile pulses "locked in…" until the next poll returns the outcome — the 2s poll latency IS the suspense beat, HQ-style
- Wrong: buzz shake + haptic on the tile, desaturates and disables, one attempt dot dims, remaining tiles stay live
- Second wrong → server flips to `round_result` (`unclear`) — exit gracefully, no client-side verdict
- Correct → `round_result` phase takeover (confetti lives there, not here)
- Audio fails to load/play: warm retry chip ("Couldn't fetch their voice — tap to retry"), never a raw error
- Deadline <15s countdown pill (shared); reconnecting pill per contract

## 7. Interaction Model
Tap play → `<audio>` plays `audio_url`; `ended` event unlocks tile reveal. Replay unlimited via chip. Tile tap → immediate optimistic lock (grid disables, picked tile pulses) → POST `/api/turn/guess` → outcome arrives via `/api/state`; on `wrong`, grid re-enables minus the dead tile. Haptic tick on tile press, buzz pattern on wrong. All transitions server-driven; the client never decides correctness.

## 8. Content Requirements
- Partner-framed prompt template, "listen first" line, locked-in line, replay chip label, audio-retry copy, attempt-dots aria-labels
- Option labels pre-localized from server (4–6; test 1-word Hindi and 3-word Bengali labels in the same grid)
- Audio: partner's `.webm` clip, ≤8s, <200KB

## 9. Recommended References (build time)
`animate.md` (tile reveal stagger, lock-in pulse, buzz), `interaction-design.md` (optimistic lock + poll-resolved outcome), `colorize.md` (tile family colors against the peacock world)

## 10. Open Questions
**Contract risk to flag to Abhilash at the 12:30 checkpoint, not patch around:** iOS Safari cannot decode webm/opus, and `audio_url` is the raw webm (the gauntlet's FLAC is async, so it may not exist when B listens). Android-first is the stated target so this may be accepted scope — if iPhone players matter, the fix is server-side (inline transcode at upload, or `audio/mp4` from iOS speakers). The frontend will feature-detect and show a warm "this round needs an Android ear 📱" fallback rather than a broken player either way.
