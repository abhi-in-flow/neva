# Design Brief — Queued Screen

Status: **confirmed** (impeccable shape, 2026-07-11)

## 1. Feature Summary
The waiting room between joining and playing: the player has just granted their mic and is being matched. It must make a 5-second wait feel electric and a 3-minute wait feel useful — while quietly doubling as the game's recruitment engine when the pool runs odd.

## 2. Primary User Action
None — the screen's job is *felt progress*: keep the player watching (leaderboard), keep the event feeling live (motion + rotating copy), and when the wait stretches, convert them into a recruiter (QR).

## 3. Design Direction
Same peacock-teal world as join; no warm-accent CTA exists here (The One Action Rule's zero case) — the warm accent is reserved for the match-found beat, where it floods in as celebration. Scene sentence: mic just granted, standing in the hall, eyes flicking between phone and venue TV, wondering who they'll get — a lobby before a game show goes live. Anchors: HQ Trivia's pre-game lobby tension, Kahoot's "players joining" room energy.

## 4. Scope
Production-ready; this one phase screen plus its match-found exit beat; shipped-quality interactivity; built against mock fixtures (including a long-wait fixture).

## 5. Layout Strategy
Top third: searching hero — pulsing voice-wave/radar motif with one rotating status line ("Finding you a worthy rival…", "Sniffing out a Hindi speaker…"). Under the status copy, show the player's declared languages as dual-script chips (mother tongue + also speak), pulled from join localStorage. Below: live leaderboard as a simple ranked list (rank, nickname, score — no cards): top N from `leaderboard_top`, player's own row pinned at bottom with their rank when not in top N. Leaderboard is the entertainment, hero is the status.

## 6. Key States
- **Searching (0–20s):** pulse motif + rotating copy pool (~6 lines, shuffled)
- **Recruiting (~20s+):** hero morphs — "Everyone's paired up! Grab a friend 👋" + join QR rendered on-screen (client-generated SVG QR of the join URL); leaderboard stays
- **Match found:** full-screen versus reveal — "YOU vs {partner}", both nicknames in display type, "You both speak {common_lang}!", warm accent floods, ~1.5s then auto-advance; tap to skip; `prefers-reduced-motion` = instant cut
- **Empty leaderboard:** "The board is empty. Be the first name on that TV."
- **Reconnecting pill** after 3 failed polls, per contract
- Leaderboard rows re-rank with subtle position transitions between polls (no flashing)

## 7. Interaction Model
Passive by design: only tap-to-skip on the versus beat and leaderboard scrolling. Phase change in `/api/state` drives everything — the versus beat is pure client theater layered over the `queued → speaking/guessing` transition, never blocking it; on tap or 1.5s the round screen is already live underneath.

## 8. Content Requirements
- Rotating searching copy pool (~6 witty lines, some language-aware from the player's declared languages)
- Recruiting copy + client-side QR generation of the join URL (tiny dependency or hand-rolled SVG)
- Versus-beat template strings; empty-board line; reconnecting pill (shared)
- Dynamic ranges: leaderboard 0 → 15 rows; nicknames up to ~20 chars (test truncation); scores 0 → 4 digits

## 9. Recommended References (build time)
`animate.md` (pulse motif, versus beat, row re-ranking), `delight.md` (copy pool, recruiting moment)

## 10. Asserted Defaults
Rotating copy changes every ~4s (not per poll); the QR encodes the same tunnel join URL as the venue TV; versus beat caps at 1.5s and never delays input on the next screen.
