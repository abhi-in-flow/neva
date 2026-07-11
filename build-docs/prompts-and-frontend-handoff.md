# PART 1 — Prompt Pack (backend: deck engine + gauntlet brain)

All prompts are templates; `{braces}` are runtime substitutions. Keep them in `deckgen/prompts.py` and `worker/prompts.py` as constants — never inline in logic code, so they can be tuned mid-event without touching pipeline code.

---

## 1.1 NB2 Lite — card image generation

**Model:** `gemini-3.1-flash-lite-image` · one call per card · retry ≤ 2 on verification failure

```
A single, clear photograph of {concept_phrase}, in an everyday {region_context}
setting in India. The {concept_noun} is the one dominant subject, centered,
occupying most of the frame, photographed at eye level in natural daylight.

Style: realistic photography, warm natural light, shallow depth of field,
clean uncluttered background typical of {region_context}.

Strict requirements:
- Exactly one dominant subject; no competing objects of similar prominence
- Absolutely NO text anywhere: no signage, labels, packaging text, posters,
  banners, or writing of any kind
- No people's faces in sharp focus (hands or backs of people are fine if
  incidental)
- No brand logos or recognizable brands
- Culturally accurate to {region_context}: local materials, local styles,
  local surroundings — not generic stock-photo Western settings
```

**Substitution table (examples):**

| var | example values |
|---|---|
| `{concept_phrase}` | "a brass water pot (kalash)", "a bamboo fish trap", "a gamosa cloth draped on a chair", "a clay tea cup (kulhad) on a wooden bench", "a hand-pulled rickshaw", "jackfruit hanging on a tree" |
| `{concept_noun}` | "water pot", "fish trap", "cloth", ... (short head noun) |
| `{region_context}` | "Assamese village", "Bengaluru urban street", "North Indian market", "rural Northeast Indian riverside" |

**Concept list guidance (`deckgen/concepts.py`):** everyday nouns and simple actions that (a) every player recognizes instantly, (b) have distinct names across languages/dialects, (c) are visually unambiguous. Good categories: kitchen objects, food items, animals, weather, farm/market objects, transport, clothing, body actions (sleeping, cooking, fishing). Avoid: abstract concepts, brands, anything text-dependent, anything regionally offensive/ambiguous. Store each concept as `{id, concept_phrase, concept_noun, label: {en, hi, as, bn, ...}}` — the multilingual labels are what B sees as options, pre-translate them at deck build time with one batched Gemini call (prompt 1.4).

---

## 1.2 Gemini 3.5 Flash — image↔label verification (deck time)

**Model:** `gemini-3.5-flash` · `thinking_level: low` · JSON schema enforced · input: generated image + label

```
You are a strict quality gate for a picture-guessing game. Players will see
this image and must recognize it as: "{label_en}".

Evaluate the attached image:

1. depicts_label: Does the image clearly and unambiguously depict
   "{label_en}" as its single dominant subject? A player glancing at it for
   2 seconds must think of "{label_en}" and not something else.
2. has_text: Is there ANY visible text, lettering, signage, or writing
   anywhere in the image?
3. has_ambiguity: Could a reasonable player name this image as a different
   common object instead? If yes, name the competing interpretation.
4. cultural_ok: Does the scene look plausibly Indian ({region_context}),
   not like Western stock photography?

Respond ONLY with JSON matching the schema. Be strict: when in doubt, fail
the image — regeneration is cheap.
```

**Response schema:**
```json
{
  "depicts_label": "boolean",
  "has_text": "boolean",
  "has_ambiguity": "boolean",
  "competing_interpretation": "string|null",
  "cultural_ok": "boolean",
  "verdict": "pass | fail",
  "reason": "string (one line)"
}
```
Accept only `verdict == "pass"` with `depicts_label && !has_text`. `cultural_ok=false` alone → regenerate with strengthened region clause.

---

## 1.3 Gemini 3.5 Flash — decoy selection (deck time, one batched call per deck)

```
You are designing wrong-answer options for a picture-guessing game played
in India. For each target concept below, choose {n_decoys} decoys FROM THE
PROVIDED CONCEPT LIST ONLY.

Good decoys are semantically adjacent (same broad category — a player who
half-understood the audio clue might plausibly pick them) but visually and
verbally distinct (no near-synonyms, no items whose name in Hindi, Assamese,
or Bengali is nearly identical to the target's name).

Targets and candidate pool:
{json_block: [{card_id, label_en}], pool: [{concept_id, label_en}]}

Respond ONLY with JSON: [{"card_id": ..., "decoy_concept_ids": [...]}]
```

Why "provided list only": decoys must be real cards with real translations already in the DB — the model must select, not invent.

---

## 1.4 Gemini 3.5 Flash — label translation (deck build, one batched call)

```
Translate each English game label below into the target languages. These
are answer options in a game played by everyday speakers in India — use
the most common, colloquial word a native speaker would actually say, not
formal/literary vocabulary. If multiple words are common, pick the most
widely understood. Keep each translation to 1-3 words.

Labels: {json_list}
Target languages: {lang_list e.g. ["hi", "as", "bn", "en"]}

Respond ONLY with JSON: [{"id": ..., "labels": {"en": ..., "hi": ..., ...}}]
```

---

## 1.5 Gemini 3.5 Flash — gauntlet triage + contamination (THE brain call)

**Model:** `gemini-3.5-flash` · `thinking_level: low` · JSON schema · input: FLAC audio + this prompt · one call per utterance (combined on purpose — halves API volume and latency)

```
You are the quality gate for a speech data collection game. A player was
shown an image of "{label_en}" and asked to describe it aloud in their
native language, which they declared as "{declared_native_lang}". The
player also knows these other languages: {common_langs}.

Analyze the attached audio recording and answer:

1. is_speech: Does the audio contain human speech (not silence, noise,
   music, or non-speech sounds)?
2. single_speaker: Is there exactly one primary speaker? (Background
   chatter far quieter than the main voice is acceptable — this was
   recorded at a live event.)
3. audio_quality_ok: Is the speech loud and clear enough that a fluent
   listener could understand it? (Reject only clearly unusable audio;
   venue noise is expected.)
4. is_label_readout: CRITICAL. The label for this image in the player's
   OTHER known languages would be approximately: {label_translations}.
   Is the player merely reading/saying that known label word (possibly
   with filler), rather than genuinely describing the image in a
   different language or dialect? Saying just the bare common-language
   word = readout. A fuller phrase or sentence in another
   language/dialect that happens to contain a borrowed word = NOT a
   readout (loanwords are normal in Indian languages).
5. apparent_language_note: In 1 short line, what language/dialect does
   this sound like to you? (Best guess, low stakes — "unsure" is fine.
   Do NOT reject based on this; the player's declaration is trusted.)
6. duration_estimate_s: Approximate speech duration in seconds.

Respond ONLY with JSON matching the schema. Bias toward ACCEPTING
borderline audio quality (the human guessing step downstream is the real
filter) but be STRICT on is_label_readout (it silently corrupts the
dataset).
```

**Response schema:**
```json
{
  "is_speech": "boolean",
  "single_speaker": "boolean",
  "audio_quality_ok": "boolean",
  "is_label_readout": "boolean",
  "readout_reasoning": "string (one line)",
  "apparent_language_note": "string",
  "duration_estimate_s": "number",
  "confidence": "number 0-1"
}
```

**Eligibility rule (in code, not in the prompt):** `training_eligible = is_speech && single_speaker && audio_quality_ok && !is_label_readout && human_validated && !duplicate`. Note the asymmetry, deliberately: lenient on quality (B's guess is the real gate), strict on contamination (nothing downstream catches it).

**Design notes worth keeping in mind:**
- `apparent_language_note` is metadata only — never a gate. Gemini judging low-resource dialect identity would reintroduce the exact bias this project exists to route around.
- Passing `{label_translations}` (all common-language forms) rather than only English makes the readout check actually work — a Hindi speaker reading the Hindi label aloud is the realistic contamination case.
- Loanword carve-out matters: Assamese/Bengali/Hindi speech legitimately borrows English nouns. Without it you'd contaminate-flag half of genuine speech.

---

# PART 2 — Frontend Handoff (Arindam / frontend agents)

**Send everything below this line to Arindam as-is.**

---

## Dialect Data Factory — Frontend Work Order

**You own:** the entire player-facing web app + the venue TV leaderboard screen. Mobile-first, QR-joinable, zero-install, works on a mid-range Android in Chrome.
**Stack:** React + Vite, plain fetch, no state library needed (one polling hook is the whole state layer). Build output is served by Abhilash's FastAPI at `/` — you develop against the mock server (below) and later just change one env var.
**Hard rule:** the backend is the source of truth for ALL game logic. The client renders state and sends three actions. If you find yourself writing an `if` about game rules, stop — that logic belongs server-side and probably already exists.

### 2.1 The one-hook architecture

Everything renders from a single polled endpoint:

```
GET /api/state          (header: Authorization: Bearer {session_token})
→ poll every 2000ms
→ response includes state_version: skip re-render if unchanged
```

Response shape (frozen contract — `contracts/api_types.py` is canonical):

```json
{
  "state_version": 41,
  "phase": "onboarding | queued | speaking_view_image | speaking_confirm_label
            | waiting_partner | guessing | round_result | session_done",
  "player": { "nickname": "...", "score": 120, "rank": 7,
              "rounds_played": 4, "rounds_cap": 20 },
  "pair":   { "partner_nickname": "...", "common_lang": "hi" } ,
  "turn": {
    "role": "speaker | guesser | null",
    "card_image_url": "/media/decks/abc.png",      // speaker only, phase-gated
    "label": { "text": "पानी का घड़ा" },            // ONLY present in speaking_confirm_label
    "options": [ {"id": 1, "text": "मछली"}, ... ],  // guesser only, 4-6 shuffled
    "audio_url": "/media/audio/xyz.webm",           // guesser only
    "attempts_left": 2,
    "deadline_ts": 1789200000
  },
  "last_result": { "outcome": "validated | wrong | unclear",
                   "points_delta": 10, "message": "..." },
  "leaderboard_top": [ {"nickname": "...", "score": 300}, ... ]
}
```

**The `phase` field IS your router.** One component per phase, a top-level switch, nothing else. Fields irrelevant to the current phase are null — never cache them across phases (the label leaking into `speaking_view_image` would corrupt the dataset; the server won't send it early, don't you hold it late).

### 2.2 The three actions

```
POST /api/join                    {nickname, native_lang, common_langs[]}
                                  → {session_token}
POST /api/pair/request            {} → 200 (then poll)
POST /api/turn/audio              multipart: file=recording.webm
                                  → {status: "ok"} | {status: "re_record", reason: "..."}
POST /api/turn/confirm-label      {} → 200
POST /api/turn/guess              {option_id} → 200 (result arrives via /api/state)
```

Errors: any 401 → back to join screen. Any 5xx/network fail → keep polling silently, show a tiny "reconnecting…" pill after 3 consecutive failures. Never show a raw error to a player.

### 2.3 Screens (in build order)

1. **Join** — nickname + "Your native language / mother tongue" + "Other languages you speak" (chips, multi-select from a fixed list: Assamese, Bengali, Hindi, English, Kannada, Tamil, Telugu, Malayalam, Marathi, Odia, Nepali, Bhojpuri, + free-text "other"). Native language is excluded from the common-language chips automatically. Big friendly copy: "Speak your language. Win points. Teach an AI."
2. **Queued** — "Finding you a partner…" + live leaderboard below (kills perceived wait).
3. **Speaker: view image** (`speaking_view_image`) — full-bleed card image + one giant **hold-to-talk** button. MediaRecorder (`audio/webm;codecs=opus`), max 8s hard stop with a visible ring timer, min 1s. On release → upload. On `re_record` → shake animation + the server's playful `reason` string ("Didn't catch that — louder! 🔊"). **No label text exists anywhere on this screen. Not in DOM, not in a hidden field. This is the single most important rule in the app.**
4. **Speaker: confirm label** (`speaking_confirm_label`) — image shrinks up, label appears: "You described: **पानी का घड़ा** — right?" [Yes, send it →] [Re-record]. (Re-record loops to screen 3 with a fresh recording.)
5. **Guesser** (`guessing`) — big play button for partner's audio (autoplay is unreliable on mobile Chrome — always require a tap), replay allowed, then 4–6 fat option buttons. Wrong pick → buzz, `attempts_left` decrements, options stay. 
6. **Round result** — confetti on `validated` (+points fly-up), gentle "no points this round" on `unclear`. Auto-advance after 2.5s.
7. **TV leaderboard** (`/tv` route, no auth) — polls `GET /api/leaderboard?top=15` every 3s. Huge type, top-3 podium treatment, a live ticker of totals: "**{n} voice clips** collected · **{k} languages**" (from `GET /api/metrics`). This screen is venue marketing — make people walk over and scan the QR (render the join QR in the corner permanently).

### 2.4 Mock server (start here, minute one)

Until the real backend lands, run this and build every screen against it:

```js
// mock/server.mjs — node mock/server.mjs (port 8787)
// Serves canned /api/state responses and cycles phase on each action POST.
// Fixture files: mock/fixtures/{phase}.json — one per phase, matching the
// contract above. Cycle order: onboarding → queued → speaking_view_image
// → speaking_confirm_label → waiting_partner → guessing → round_result → loop.
// POST endpoints return the canned success and bump the phase pointer.
// Include one fixture where /api/turn/audio returns re_record, and one
// guessing fixture with attempts_left: 1.
```

Write the eight fixture JSONs by hand from the contract — 20 minutes, and it means zero integration risk: when the real API is ready you change `VITE_API_BASE` and delete nothing.

**Integration checkpoint: 12:30 PM.** Abhilash's game-core smoke test green = you point at the real backend. Anything that breaks at that moment is a contract violation — flag it, don't patch around it.

### 2.5 Recording specifics (the only genuinely fiddly part)

- `navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}})` — request permission on the **join screen** (with friendly framing: "We need your mic — that's the whole game! 🎤"), not mid-round.
- `MediaRecorder` with `audio/webm;codecs=opus`; fall back to default mimeType if unsupported (iOS Safari gives `audio/mp4` — send it anyway, backend transcodes everything).
- Hold-to-talk = pointerdown/pointerup + pointerleave safety. Also handle: user holds < 1s (toast "hold longer!" — don't upload), tab backgrounded mid-recording (discard).
- Upload as multipart immediately on release with a spinner on the button itself; the 8s cap keeps files < 200KB so this is fast even on bad venue Wi-Fi.

### 2.6 Visual direction (one line so we don't bikeshed)

Playful, chunky, high-contrast, thumb-sized targets, one accent color, system fonts, zero component libraries. Every screen readable at arm's length in a noisy hall. Hindi/Assamese text must render at full size — test Devanagari + Assamese script on your fixture data early, not at 4 PM.

### 2.7 What you do NOT build

No auth beyond the bearer token. No settings. No profile. No history screen. No dark mode. No i18n framework (UI chrome is English; game content arrives pre-localized from the server). No service worker. No websockets. Every one of these is a 5 PM deadline eaten alive.

### 2.8 Your second hat (if frontend is done early): deck engine

Work order C in the main plan (NB2 Lite deck generation) is yours if you're ahead — it's a standalone CLI with zero frontend coupling, and its throughput logging is our Track 3 demo centerpiece. Prompts are pre-written in Part 1 of this doc.
