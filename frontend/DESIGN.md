---
name: Dialect Data Factory
description: A live voice-guessing party game where speaking your dialect scores points — warm, witty, competitive.
colors:
  peacock-deep: "oklch(26% 0.055 205)"
  peacock-surface: "oklch(32% 0.06 202)"
  peacock-raised: "oklch(38% 0.065 200)"
  peacock-line: "oklch(44% 0.06 200)"
  ink: "oklch(96.5% 0.01 190)"
  muted: "oklch(80% 0.035 195)"
  marigold: "oklch(80% 0.15 75)"
  marigold-deep: "oklch(70% 0.15 70)"
  ink-warm: "oklch(22% 0.04 230)"
  tile-rani: "oklch(52% 0.19 356)"
  tile-sky: "oklch(50% 0.11 230)"
  tile-leaf: "oklch(50% 0.12 150)"
  tile-terra: "oklch(50% 0.15 35)"
  failure-surface: "oklch(34% 0.07 45)"
  failure-edge: "oklch(58% 0.13 55)"
typography:
  display:
    fontFamily: "Bricolage, system-ui, sans-serif"
    fontSize: "3.25rem"
    fontWeight: 800
    lineHeight: 1.1
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Bricolage, system-ui, sans-serif"
    fontSize: "2.5rem"
    fontWeight: 800
    lineHeight: 1.1
    letterSpacing: "-0.015em"
  title:
    fontFamily: "Bricolage, system-ui, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 800
    lineHeight: 1.1
  body:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 600
    lineHeight: 1.4
rounded:
  sm: "10px"
  md: "16px"
  lg: "24px"
  full: "999px"
spacing:
  s1: "0.25rem"
  s2: "0.5rem"
  s3: "0.75rem"
  s4: "1rem"
  s5: "1.5rem"
  s6: "2rem"
  s7: "3rem"
  s8: "4rem"
components:
  button-cta:
    backgroundColor: "{colors.marigold}"
    textColor: "{colors.ink-warm}"
    typography: "{typography.title}"
    rounded: "{rounded.lg}"
    height: "60px"
    padding: "0 1.5rem"
  button-cta-active:
    backgroundColor: "{colors.marigold-deep}"
    textColor: "{colors.ink-warm}"
  button-talk:
    backgroundColor: "{colors.marigold}"
    textColor: "{colors.ink-warm}"
    rounded: "{rounded.full}"
    size: "96px"
  chip:
    backgroundColor: "{colors.peacock-surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    height: "52px"
    padding: "0.25rem 0.5rem"
  chip-selected:
    backgroundColor: "{colors.peacock-raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
  tile-answer:
    backgroundColor: "{colors.tile-rani}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "76px"
    padding: "0.75rem 1rem"
  input-text:
    backgroundColor: "{colors.peacock-surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0.75rem 1rem"
  board-row:
    backgroundColor: "{colors.peacock-surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0.75rem 1rem"
  toast:
    backgroundColor: "{colors.peacock-raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0.75rem 1rem"
---

# Design System: Dialect Data Factory

## 1. Overview

**Creative North Star: "The Festival Game Stall"**

A game stall at an Indian festival, rebuilt as an app: a deep peacock-teal night-market world lit by one marigold action color and four saturated answer-tile hues, with a chunky display face doing the shouting and the system sans doing the talking. Kahoot's fat answer tiles, Duolingo's juicy feedback and blame-free misses, HQ Trivia's live-event tension — grounded in an unmistakably Indian palette. The system now exists as working code: every value in this document is extracted from `web/src/styles/`, and every color pair was contrast-verified numerically before it shipped.

The system rejects generic AI-startup slop (no purple-blue gradients, glassmorphism, or sparkle decoration), childish edtech (no mascots), corporate dashboards (no gray cards or tiny type in the player's face), and Western stock-photo minimalism. Players are adults in a noisy hall, one thumb free.

**Key Characteristics:**
- The peacock world is constant; marigold appears exactly once per screen, on the primary action
- Four answer-tile hues, always paired with position and full-size text
- Fixed rem type scale (1.2 ratio); Bricolage 800 for shoutable words, system sans for everything else; Indic game content in system Noto ≥24px, never restyled
- Motion is choreographed at the beats (versus reveal, verdict flood) and instant everywhere else; everything that animates in settles
- Verified: ink/bg 13.7:1 · muted/bg 8.2:1 · ink-warm/marigold 9.0:1 · ink/tiles ≥5.1:1

## 2. Colors

Full palette on a committed dark-teal surface: the world is peacock, the action is marigold, the answers are four festival hues.

### Primary
- **Peacock Deep** (oklch(26% 0.055 205) / #002b31): the body background of every surface — phone and TV. The app "is" this color; it makes the tiles and marigold glow and reads as a stage backdrop at TV scale.
- **Peacock Surface** (oklch(32% 0.06 202) / #003b40) and **Peacock Raised** (oklch(38% 0.065 200) / #004c50): panels/inputs and pressed/hover states — depth by tonal layering, same hue family. **Peacock Line** (oklch(44% 0.06 200) / #225c60) is the only border color.

### Secondary
- **Marigold** (oklch(80% 0.15 75) / #f5ae39): the One Action color — hold-to-talk, CTAs, points, the QR frame, rank #1. Text on it is always **Ink Warm** (oklch(22% 0.04 230) / #031e29), 9:1. **Marigold Deep** (oklch(70% 0.15 70) / #d98b09) is its pressed state.

### Tertiary
- **The tile family** — Rani (oklch(52% 0.19 356) / #b5226c), Sky (oklch(50% 0.11 230) / #006d95), Leaf (oklch(50% 0.12 150) / #21763c), Terra (oklch(50% 0.15 35) / #a7391e): guesser answer tiles and the TV podium. All carry Ink text at ≥5.1:1.
- **Failure Surface / Edge** (oklch(34% 0.07 45) / #552b17 · oklch(58% 0.13 55) / #b3621e): warm-failure panels (mic denied, audio retry). Warm and dim, never red-alert.

### Neutral
- **Ink** (oklch(96.5% 0.01 190) / #ecf6f5): primary text, 13.7:1 on Peacock Deep.
- **Muted** (oklch(80% 0.035 195) / #a5c5c5): secondary text and quiet labels, 8.2:1 — never used on colored fills.

### Named Rules
**The One Action Rule.** Marigold appears on exactly one element per screen: the primary action. Screens with no action (queued, waiting) have no marigold control — its absence is what makes the celebration floods land.

**The Never-Alone Rule.** Tile colors are always paired with position and a full-size text label. Color celebrates; it never carries meaning alone.

**The No-Red Rule.** Failure is warm, not alarming: the failure pair above, playful copy, zero ❌ iconography anywhere in the app.

## 3. Typography

**Display Font:** Bricolage Grotesque 800 (self-hosted 38KB latin woff2, `font-display: swap`; falls back to system-ui)
**Body Font:** System sans (`system-ui, -apple-system, 'Segoe UI', Roboto`)
**Game-content scripts:** Devanagari / Bengali–Assamese render in system Noto — the display face is never applied to Indic game content.

**Character:** Loud where it counts, invisible everywhere else. Bricolage carries the shoutable words — scores, verdicts, phase titles, CTAs, TV names; the system sans carries every sentence.

### Hierarchy
- **Hero** (800, 3.25rem, 1.1, −0.02em): join tagline, versus names, verdict words, session finale.
- **Headline** (800, 2.5rem): step titles, confirm labels, "Finding you a partner".
- **Title** (800, 1.75rem): CTAs, searching hero, mic-denied heading.
- **Body** (400, 1.125rem, 1.5): instructions, form copy — 18px floor, the Arm's-Length Rule's minimum.
- **Label** (600, 0.875rem): chips, field labels, hints, the HUD.
- **Game content** (system Noto, ≥1.5rem/24px): card labels and answer options — full size, everywhere, no exceptions.
- **TV scale**: the phone ceiling does not apply on `/tv` — podium names run 44–56px, the pitch 84px, tuned for 8-meter legibility.
- Numbers are always `tabular-nums` (scores, ranks, timers).

### Named Rules
**The Arm's-Length Rule.** If a player standing in a noisy hall can't read it at arm's length, it's too small. Body never drops below 18px; game content never below 24px.

## 4. Elevation

Flat by default, tonally layered: depth comes from the peacock ramp (deep → surface → raised), not shadows. Shadows exist in exactly two structural roles: the sticky CTA casts an upward fade (`0 -12px 24px -8px` in bg color) to signal content scrolling beneath it, and the TV crown carries a small drop shadow to sit above its podium. Overlay moments (versus, verdict) are full-bleed color floods rather than floating panels.

### Named Rules
**The Settle Rule.** Everything that animates in visibly lands — rings stop, tiles settle, banners rest. Floating at rest is forbidden. Motion beats use `--ease-out-quint`/`expo`, 120/240/420ms tokens; every animation has a reduced-motion fallback.

## 5. Components

### Buttons
- **CTA** (`.cta`): marigold fill, Ink Warm text, Bricolage 800, 24px radius, ≥60px tall, sticky at the viewport bottom in flows; press = `translateY(2px)` + Marigold Deep. One per screen.
- **Hold-to-talk** (`.talk`) — the signature control: 96px marigold circle, `touch-action: none`, breathes with live mic level (`scale(1.04 + level×0.14)`), 8s ring timer drawing around it (`.ring`, always `pointer-events: none`), one-time coach pulse on first turn.
- **Play hero** (`.play`): the guesser's mirror of the talk button — same scale and family, progress ring, compacts to a replay chip after first listen.
- **Quiet secondary** (`.retake`, `.back-link`): muted text, no fill, ≥48px target.

### Chips
- **Language chips** (`.chip`): Peacock Surface, 2px Line border, 10px radius, ≥52px, dual-script stacked labels (English 700 + native script in Muted). Selected = Raised fill + marigold border; press = `scale(0.95)`.

### Cards / Containers
- Surfaces, not cards: leaderboard rows, toasts, and panels are single-level Peacock Surface blocks with 10–16px radii and no borders (except the warm-failure panel's 2px Failure Edge). Nothing nests. The card image frame (`.card-frame`) is the one image container: 24px radius, absolute-fill `object-fit: cover` image, flex-basis seam transition to a 150px thumbnail in the confirm phase.

### Inputs / Fields
- Peacock Surface fill, 2px Line border, 16px radius, body-size text; focus = border swaps to marigold (no double ring — the global `:focus-visible` marigold outline covers everything else at 3px/2px offset).

### Navigation
- None. The server's `phase` field is the router; one component per phase, transitions are the design (slide track on join, seam on speaker, takeovers for beats). The persistent HUD (`.hud`: nickname · rounds · marigold score) is the only chrome, and it renders server truth only.

### Answer Tiles (signature)
- `.tile`: family-colored fills, Ink text ≥24px Noto, 16px radius, ≥76px tall, 70ms staggered entrance, thumb-zone anchored. States: locked (pulse + ink outline + "locked in…"), dead (desaturate 0.15 + buzz shake + disabled), grid falls to one column when any label exceeds ~14 chars.

## 6. Do's and Don'ts

### Do:
- **Do** keep exactly one marigold action per screen (The One Action Rule) — and zero on screens with nothing to press.
- **Do** pair every tile color with position and a full-size label (The Never-Alone Rule).
- **Do** render Indic game content in system Noto at ≥24px, everywhere, unstyled by the display face.
- **Do** keep `pointer-events: none` on every decorative SVG overlay (rings) — a ring that eats a tap already shipped as a bug once.
- **Do** verify contrast numerically when adding colors: body ≥4.5:1, large ≥3:1, and text-on-fill pairs checked against the ramp (`ink` on tiles, `ink-warm` on marigold).
- **Do** write failure states warm and playful on the Failure Surface pair — re-records tease, never blame.
- **Do** give every animation a `prefers-reduced-motion` fallback and let everything settle (The Settle Rule).

### Don't:
- **Don't** use generic AI-startup slop: no purple-blue gradients, no glassmorphism, no sparkle emojis, no "AI magic" framing (PRODUCT.md anti-reference, verbatim).
- **Don't** go childish edtech: no mascots, no primary-school aesthetics — players are adults at a tech event.
- **Don't** build corporate dashboard surfaces: no gray cards, tiny type, or data density in the player's face; factory telemetry stays off the TV ticker.
- **Don't** default to Western stock-photo minimalism — the culturally rooted card imagery is the only imagery.
- **Don't** let the card label exist anywhere — DOM, hidden field, cached state — before the server's confirm-phase payload. The recording stage component has no label prop by design; keep it that way.
- **Don't** use red for any failure state, or ❌ iconography anywhere.
- **Don't** animate anything that blocks input on a mid-range Android; beats cap at 2.5s and are always tap-skippable.
