# Design Brief — Payoff Arc (waiting_partner → round_result → session_done)

Status: **confirmed** (impeccable shape, 2026-07-11)

## 1. Feature Summary
The game's emotional payoff, shaped as one arc: the speaker's suspense while their clip is judged (`waiting_partner`), the shared verdict moment both players hit simultaneously (`round_result`), and the session-scale finale at the round cap (`session_done`). One motion vocabulary and one copy voice across all three — anticipation, verdict, celebration.

## 2. Primary User Action
None until the exit — these are *felt* screens: tension (waiting), release (result), pride (done). Tap-to-skip is the only input.

## 3. Design Direction
The warm accent's biggest moments: on `validated` it floods the screen (same flood vocabulary as the versus reveal — the app's two celebration beats rhyme). `unclear` stays in the peacock world — warm-toned copy, visually quiet, no red, no ❌, no failure theater. Scene sentence: two strangers across a hall both staring at their phones waiting for the same verdict — when it lands, one of them should want to look up and find the other. Anchors: Duolingo's correct-answer juice and blame-free misses, Kahoot's between-question verdict screens.

## 4. Scope
Production-ready; three phase screens sharing one component family; mock fixtures for all three outcomes plus session_done.

## 5. Layout Strategy
- **waiting_partner:** listening motif from queued, partner-framed — "Your voice is with {partner}…"; if state exposes `attempts_left`, the two attempt dots render here, dimming as B burns tries ("2 tries left…"); rotating suspense copy otherwise. Quiet, center-weighted.
- **round_result:** full-screen verdict takeover. Outcome word huge in the display face (VALIDATED! / "So close" / "Too tricky"), `points_delta` flies up into the persistent score, server `message` beneath in body type. Role-flavored line: speaker "*{partner} understood your Assamese!*", guesser "*You got it!*". Confetti burst on validated only. 2.5s draining ring wraps the outcome word; tap anywhere skips.
- **session_done:** the verdict stage at session scale — "That's your 20 rounds, {nickname}!" with final score, rank, the pride line ("Your voice just taught an AI {n} clips of {native_lang}"), leaderboard beneath, closing nudge to watch the TV board.

## 6. Key States
- waiting_partner: default suspense; attempt-dot decrement beat (if data present); long-wait copy rotation
- round_result × 3: `validated` (accent flood + confetti + fly-up), `wrong` (guesser's final miss — brief, honest, warm), `unclear` (gentle teal, no-points copy, zero blame)
- Draining ring → auto-advance; tap-to-skip (shared pattern with versus reveal); `prefers-reduced-motion`: no confetti/flood, instant crossfade, outcome full-size
- session_done: terminal — polling continues, no further rounds; leaderboard keeps updating live
- Reconnecting pill per contract on all three

## 7. Interaction Model
Entirely server-driven phase changes; only inputs are tap-to-skip (round_result) and leaderboard scroll (session_done). Points fly-up animates the *delta* while the persistent score header (shared across game screens) receives it — score never desyncs from server truth; animation is presentation only. Confetti is a bounded canvas burst (~1.5s), never blocking, never looping.

## 8. Content Requirements
- Suspense copy pool for waiting_partner (~4 lines); role-flavored verdict templates × 3 outcomes × 2 roles; server `message` renders verbatim beneath
- session_done templates (score, rank, clips-taught pride line; ranges: 1–20 rounds, 0–4-digit scores)
- Confetti asset (tiny canvas lib or hand-rolled, brand palette colors)

## 9. Recommended References (build time)
`animate.md` (flood, fly-up, draining ring, confetti), `delight.md` (verdict copy voice, the look-up-and-find-them moment)

## 10. Asserted Defaults
`wrong` renders in the same gentle family as `unclear` (never punitive); session_done has no "play again" (caps are server-enforced; celebrate completion, don't tease a locked door); attempt dots on waiting_partner degrade gracefully to suspense copy if the state omits `attempts_left`.
