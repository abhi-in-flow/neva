# Design Brief — Join Flow

Status: **confirmed** (impeccable shape, 2026-07-11)

## 1. Feature Summary
The join flow is the front door: a player scans the QR on the venue TV, lands here, and must be in the matchmaking queue within ~30 seconds. It collects nickname, native language, and other spoken languages, secures mic permission, and sets the game's tone — the first proof that "speak your language, win points" is a game, not a survey.

## 2. Primary User Action
Complete two quick steps and press the one warm-accent CTA that simultaneously grants mic access and joins the game.

## 3. Design Direction
Full palette on the peacock-teal base world per DESIGN.md; the warm marigold-family accent appears exactly once per step (Next / Let's play — The One Action Rule). Scene sentence: standing in a bright, noisy hackathon hall, phone in one hand seconds after scanning a QR, curious and slightly impatient — the screen should feel like a glowing game ticket, not a form. That forces the committed deep-teal surface with high-luminance type. Anchors: Kahoot's join-lobby energy, Duolingo's friendly form moments, HQ Trivia's live-event arrival feel.

## 4. Scope
Production-ready fidelity; this one flow (two steps + mic moment); shipped-quality interactivity with real `getUserMedia` handling; polish until it ships, built against mock fixtures first.

## 5. Layout Strategy
Two steps, each fitting one phone screen with zero scroll:

- **Step 1 — identity:** Tagline hero in the display face ("Speak your language. Win points. Teach an AI."), pre-filled generated nickname (large, editable, shuffle die button), native-language selection. Warm CTA "Next" in thumb reach.
- **Step 2 — languages:** "What else do you speak?" chip grid of the fixed 12 (each chip: English + own script, e.g. *Assamese · অসমীয়া*), native language auto-excluded, free-text "other" chip last, ≥1 required. CTA "Let's play 🎤" with mic microcopy beneath ("We'll ask for your mic — that's the whole game!").

Minimal step indicator (1/2). Keyboard never covers a CTA (text entry only on step 1's upper half).

## 6. Key States
- Default step 1 / step 2, slide transition (settles per The Settle Rule)
- Nickname shuffle (re-roll flip on the name)
- Validation: empty nickname, no native language, zero common languages — inline, playful
- CTA pressed → mic permission pending (pulsing "listening for permission…" button state)
- **Mic denied** — warm-failure panel with Chrome re-enable steps + retry; never a raw browser error
- Join POST in-flight (spinner in button); network/5xx → silent retry + "reconnecting…" pill
- Returning player: persisted session token → skip join, straight to polling (401 bounces back here)

## 7. Interaction Model
Tap-first: chips toggle with spring-scale tick, native-language pick auto-advances, shuffle re-rolls with a quick flip. Final CTA = one gesture, three acts: request mic → on grant POST `/api/join` → store token → enter polling loop (hand off to Queued). Mic grant gets a micro-celebration beat (button flashes success).

## 8. Content Requirements
- Tagline + step titles; UI chrome in English
- Curated generated-name list (~50 TV-safe playful names, Indian flavor — *Chai Champion*, *Bijli Baaz* style) as a static frontend asset
- Language list with native-script renderings for all 12 (day-one Devanagari + Bengali–Assamese rendering test)
- Mic explainer, mic-denied recovery copy, three validation lines, reconnecting pill copy
- No images; the hero is typographic

## 9. Recommended References (build time)
`interaction-design.md`, `animate.md`, `delight.md`

## 10. Asserted Defaults
≥1 common language required, no pre-selection (no English bias); session token in `localStorage`; step indicator "1/2", not a progress bar.
