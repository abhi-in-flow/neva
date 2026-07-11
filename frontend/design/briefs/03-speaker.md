# Design Brief — Speaker Flow (view-image → record → confirm-label)

Status: **confirmed** (impeccable shape, 2026-07-11)

## 1. Feature Summary
The heart of the game and the reason the corpus exists: speaker A sees a culturally grounded card image, holds a button, and describes it aloud in their native language — then confirms the label only *after* the audio is accepted. This flow carries the app's single most important rule (the label must not exist anywhere client-side until the server sends it) and its only genuinely fiddly engineering (MediaRecorder).

## 2. Primary User Action
Hold the big warm button and speak — one gesture, 1–8 seconds, thumb never leaves the bottom of the screen.

## 3. Design Direction
Full-bleed card image over the peacock world; the giant circular warm-accent hold-to-talk button is the One Action, bottom-center in the thumb zone. Scene sentence: phone gripped in one hand in a loud hall, partner waiting across the room, three seconds to think of the word your grandmother would use — the screen must feel like a mic in your fist, not a form. Anchors: WhatsApp voice-note hold-to-talk semantics, HQ Trivia's it's-happening-now tension, Duolingo's warm re-try moments.

## 4. Scope
Production-ready; the whole speaker flow (view-image, recording, uploading, confirm-label, re-record loop); shipped-quality MediaRecorder handling with all edge cases; mock fixtures first, including a `re_record` fixture.

## 5. Layout Strategy
- **View-image:** card image owns ~65% of viewport; quiet always-present instruction line ("Hold the button — describe this in Assamese"); giant button (~96–112px circle) bottom-center. Nothing else.
- **Recording (held):** image dims slightly; 8s ring timer draws around the button; button breathes with live mic input level — visible proof it hears you.
- **Confirm-label:** image shrinks upward to a thumbnail; label arrives big beneath in full-size Noto ("You said it's: **পানীৰ ঘট** — right?"); local-blob replay button; warm-accent "Yes, send it →" + quiet "Re-record". The shrink-up transition is the seam making two states feel like one flow.

## 6. Key States
- View-image default; **first-turn-ever coach**: one-time pulsing hint on the button, never shown again
- Recording (held): ring timer + input-level breathing
- Released <1s: playful toast ("Hold longer — give it a full breath!"), nothing uploaded
- **8s hard stop:** auto-release + auto-upload, "Time's up — sending!" beat
- Pointer leaves button mid-hold: recording discarded, "Keep your thumb on the button!" toast
- Tab backgrounded mid-recording: discard silently, reset
- Uploading: spinner inside the button, disabled
- **`re_record`:** shake animation + server's playful reason verbatim; button resets armed
- Confirm-label: label + replay-own-clip + confirm/re-record; re-record loops to view-image for a fresh take
- Deadline <15s: countdown pill fades in (hidden before); deadline passed server-side → phase changes, exit gracefully
- Mic lost mid-game (getUserMedia fails): warm-failure panel, shared vocabulary with join's mic-denied
- Reconnecting pill per contract

## 7. Interaction Model
`pointerdown` starts MediaRecorder (`audio/webm;codecs=opus`, iOS `audio/mp4` fallback sent as-is); `pointerup` stops and uploads immediately as multipart; `pointercancel`/`pointerleave` discards. Haptic tick (`navigator.vibrate` where available) on record start and accept. Accepted upload gets a small success beat before advancing to confirm-label. On confirm, POST `/api/turn/confirm-label`, hand off to waiting-partner. **Label blackout is architectural, not stylistic:** the view-image component never receives a label prop; the label exists only in the confirm-phase payload, rendered by a different component, never cached.

## 8. Content Requirements
- Instruction line template ("Hold the button — describe this in {native_lang}"), first-turn coach line
- Too-short toast, pointer-slip toast, time's-up line, uploading label, confirm question template, replay/confirm/re-record labels
- Server-supplied `re_record` reasons render verbatim (contract)
- Card image from `/media/decks/...` (always exactly 1; skeleton for slow loads)
- Label text in any of 12+ languages/scripts at ≥24px — layout must absorb a 3-word Bengali label

## 9. Recommended References (build time)
`animate.md` (ring timer, breathing button, shrink-up seam), `interaction-design.md` (pointer state machine), `delight.md` (success beats, haptics)

## 10. Asserted Defaults
Mic input level drives button breathing via a lightweight `AnalyserNode` on the existing stream; replay-own-clip plays the local blob (no server round-trip); countdown pill threshold 15s; the 8s cap auto-**sends** rather than discarding.
