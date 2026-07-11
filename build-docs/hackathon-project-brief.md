# Handoff: Dialect Data Factory — Google DeepMind Bangalore Hackathon
**From:** Arindam (pitch owner) · **To:** Abhilash (CTO / architect)
**Status:** Concept frozen. This document is planning territory only — no code before 10:30 AM Saturday (event rule: new work only, public repo).

---

## 1. One-paragraph concept

A multiplayer, turn-based charades game that converts bored, linguistically diverse crowds into a **self-annotating, self-cleaning dialect speech corpus factory**. Player A sees an AI-generated image (with a system-known semantic label), describes it aloud in their native dialect/low-resource language; a paired stranger B — who shares a common major language but *not* A's native tongue — sees only common-language text options and must pick the right one from A's audio alone. A correct guess validates the pair `dialect audio ↔ common-language text` with the image as semantic ground truth. Validated pairs flow zero-touch through a cleaning gauntlet into a same-day Gemma fine-tune. Deployment thesis: hospitals (captive, diverse, bored populations; your trust hospital = owned pilot site). Live deployment on Saturday: **the hackathon venue itself** — attendees are the first players, and the corpus they generate powers the final-round demo.

**Headline claim (Tier 1, locked):** throughput economics, not model performance. "N validated dialect pairs, K languages, X hours, ₹Y per sample, zero manual annotation."

---

## 2. Prize strategy

- **Primary submission: Track 3 (NB2 Lite — High-Throughput Creative Workflows).** The picture-deck engine is the qualifying pipeline: NB2 Lite generates infinite, culturally-grounded, per-region image decks on demand. Speed/cost is load-bearing — fresh decks per round prevent memorization/farming and enable regional customization. Frame it exactly this way in the README; the track bar explicitly rejects prompt-box-to-image apps and rewards programmatic pipelines.
- **Second flag: Gemma 4 special prize ($2K, likely thin competition).** The same-day fine-tune is the payoff beat. If time allows, on-device inference strengthens the "offline hospital ward" narrative — but this is explicitly **cut-first scope** (see §8).

---

## 3. Core game loop (the mechanic IS the annotation)

1. **Pairing:** match two players who share a common major language (Hindi/English/Assamese etc.) but declare *different* native tongues. Strangers only — no self-pairing, no same-native-language pairing. Language declaration at onboarding.
2. **Elicitation (A's turn):** show image **first, alone**. A speaks their description in their native tongue (push-to-talk, phone to mouth). **Only after audio is captured**, reveal the semantic label / common-language text for tap-to-confirm. Sequence is non-negotiable — showing text before speech contaminates the corpus with literal translations.
3. **Validation (B's turn):** B hears A's audio and sees a grid of 4–6 common-language text options (decoys drawn from the same deck). Correct pick = pair validated. B never sees the image.
4. **Scoring:** points on validated pairs only (kills lazy/garbage clue incentive). Turn-based alternation. Leaderboard. Saturday reward = leaderboard glory; hospital deployment reward = vending machine / canteen credits.

**Why the design works (for your intuition while building):** the image is machine-known ground truth, so no human tagging step exists; B's incomprehension of A's language *proves* the utterance is genuinely dialectal; B's correct guess proves it carried meaning. Humans only do the two things they're irreplaceable for — speaking naturally and comprehension-checking. Everything else is machine-verifiable.

---

## 4. Golden data record (design the whole pipeline backwards from this)

```
{
  utterance_id, audio_ref,
  native_lang_tag (self-declared),
  common_lang_text (system label, not player-produced),
  image_id, deck_id,
  validation: { guesser_id, correct: bool, attempts },
  quality: { snr_flag, duration_s, dedup_hash, contamination_flag },
  speaker_meta: { player_id, declared_region (optional), session_id },
  timestamps
}
```
Output must be shardable and directly consumable by the LoRA harness with zero manual steps. The stage line depends on it: *"No human touched the data between a player speaking and the model learning."*

---

## 5. Cleaning gauntlet (every utterance passes through, in order)

1. **Gemini audio triage:** is it speech; single speaker; duration 1–8s; SNR sane. Failure → in-game re-record prompt styled as game feedback ("Didn't catch that — louder!"), never an error state.
2. **Human gate:** B correct = validated. One wrong guess = retry allowed; two wrong = flag `unclear`, no points, keep for research but exclude from training set.
3. **Anti-farming:** near-duplicate audio dedup per player; per-player daily/session caps; diminishing returns.
4. **Contamination check (subtle, important):** Gemini verifies the utterance is not simply the common-language label read aloud. This is the difference between a dialect corpus and an accent corpus.
5. **Structuring:** auto-package to the golden record and shard.

---

## 6. Stack mapping

| Component | Role |
|---|---|
| **NB2 Lite** (`gemini-3.1-flash-lite-image`) | On-demand picture-deck generation, culturally grounded (Indian domestic/rural/urban scenes — not stock-photo America). Pre-write the deck *prompt strategy* now (planning); generate decks live Saturday. Speed = anti-memorization + per-region decks. This is the Track 3 qualifying pipeline — instrument and surface its throughput in the demo. |
| **Gemini 3.5 Flash** | Image↔label consistency verification at deck-generation time; audio triage; contamination check; decoy-option selection; general orchestration. |
| **Gemini Audio / Live API** | Capture pipeline; optional "the app tries to understand you" flourish. Keep minimal — not load-bearing. |
| **Gemma 4 (E2B class)** | LoRA fine-tune on the day's validated corpus. Harness built and tested on dummy data by ~2 PM so real corpus slots in at 4 PM. |

---

## 7. Eval policy (decided, do not relitigate at 6 PM)

- **Tier 1 (headline, always shipped):** pipeline throughput + unit economics. Bulletproof at any n.
- **Tier 2 (theatre, back pocket):** 5 held-out utterances, base Gemma vs. tuned Gemma, qualitative side-by-side on stage. No percentages claimed. Deploy only if the tune visibly behaves.
- **Tier 3 (dead unless undeniable):** quantitative delta on a 20% holdout. Compute it; mention it only if unambiguous. If mushy or negative, it never existed.
- **Insurance corpus (rules-legal):** during hacking hours, we two record 50–100 seed samples ourselves (Assamese from Arindam et al.). Optional Gemini synthetic augmentation, clearly labeled as synthetic — on-theme, honest.

Pitch framing: under-claim, over-demonstrate. "We're not claiming we solved Goalpariya in six hours; we built the machine that will — and it's running."

---

## 8. Saturday timeline & scope discipline

| Time | Abhilash | Arindam |
|---|---|---|
| 10:30–1:00 | Repo public immediately. Pairing logic, capture pipeline, storage, cleaning gauntlet skeleton. Web app, QR-joinable, zero-install. | Game UX, NB2 Lite deck generation + prompt tuning, leaderboard screen. Print QR codes the moment the repo is live. |
| 1:00 (lunch) | Keep pipeline stable under load. | **Launch window — go table to table.** Cold-start is make-or-break. |
| 1:00–4:00 | LoRA harness built + tested on dummy corpus, in parallel. Monitor gauntlet output quality. | Seed gameplay, community management, collect demo screenshots/metrics. |
| 4:00 | **Go/no-go on Tier 2.** Freeze corpus, run tune, run holdout. | Finalize pitch with real numbers. |
| 5:00 | Submit (public repo, 1-min video, all members on submission). | Video + submission copy. |
| 5:00–8:00 | Keep game live — judging-round data still counts for the final-round story. | Round 1 pitch; if top 6, final-round close with live numbers. |

**Cut order if behind:** on-device Gemma inference → Live API flourishes → leaderboard polish → Tier 2. **Never cut:** the game loop, the gauntlet, throughput instrumentation, the NB2 Lite deck engine (it's the track qualification).

**UX decisions already locked:** push-to-talk; image-before-label sequencing; re-record framed as gameplay; stranger-pairing by declared languages.

---

## 9. Known risks (red-teamed)

1. **Lunch cold-start fails / thin corpus** → Tier 1 framing survives any n; venue = "pilot proving the mechanic," hospital = scale thesis.
2. **Venue noise** → push-to-talk + triage + re-record loop.
3. **"Just a data-collection app"** → the counter is the zero-touch pipeline + NB2 Lite engine visibly working in the demo; spend real demo seconds on the machinery, not only the story.
4. **Farming for points** → validation-gated points, dedup, caps.
5. **Fine-tune misbehaves** → Tier 2 was never promised; Tier 1 stands alone.
6. **Rules exposure** → repo public from minute one; all code written in-window; pre-work stays on paper (this doc); insurance corpus recorded during hacking hours only; demo script explicitly delineates what was built today.

---

## 10. Distribution story (for your Q&A pocket)

Owned pilot: your trust hospital — we can greenlight deployment ourselves, no hypothetical partner. Policy tailwind: NE state governments have already named the healthcare language barrier as a problem. Reward loop in hospital: points → vending/canteen credits; hospital's incentive: occupied patients now, multilingual comms tooling later. Unit economics line: "we buy validated dialect data for the price of a biscuit packet."
