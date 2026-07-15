# Neva — Research positioning

**One-line thesis:** communicative success is a supervision signal. Whether a listener recovers
the intended concept — and *which* listener recovers it — can stand in for paid annotation of
speech quality and dialect.

This document does three things: states what Neva is testing, places it against prior work, and
marks the one claim that is genuinely unclaimed. It is deliberate about what is **not** novel, so
the contribution is stated precisely rather than broadly.

> **Built vs. proposed.** The hackathon pipeline validates concepts **across languages** (two
> players, different mother tongues, shared bridge language). The research question below is one
> step narrower — the same loop run **within a language, across dialects** — and is a
> **direction, not a built feature.** See [`ROADMAP.md`](ROADMAP.md).

---

## The research question

In a dialect-diverse language (Assamese: Kamrupi, Goalpariya, Central/Nagaon, Eastern/Sivasagar,
plus contact varieties Nagamese and Sadri), can gameplay outcomes act as:

- **H1 — a quality signal.** Does *guess-success* predict human-rated utterance quality better
  than an off-the-shelf ASR-confidence baseline (Whisper / MMS)?
- **H2 — a dialect signal.** Does *who* succeeds leak dialect? If a Goalpariya speaker's clue is
  solved quickly by Goalpariya listeners and slowly by Sivasagar listeners, the differential
  accuracy/latency across listener geography functions as a dialect label — and yields a
  perceptual distance matrix between varieties at no annotation cost.

**H2 is the contribution.** H1 is the sanity check that makes H2 credible.

---

## Prior work

Three bodies of work touch Neva. Two of them cover ground we might otherwise claim as novel.
Similarity is rated ★☆☆☆☆–★★★★★ against Neva's full idea (game-elicited speech → free
comprehension-derived labels → dialect signal).

### 1. Gamified & citizen-science collection

| Work | What it is | What it doesn't do | Similarity |
|---|---|---|---|
| [Dia-Lingle (ACL 2025)](https://aclanthology.org/2025.acl-demo.15/) — Sun, Sevastjanova, Ahmadi, Sennrich, El-Assady | Closest published work. Players rewrite sentences into their dialect; a classifier guesses the dialect and solicits feedback. Combines active learning with gamified difficulty. | **Text only.** No audio — and dialects exist predominantly in speech. | ★★★★☆ |
| [GWAP lineage](https://aclanthology.org/2022.nidcp-1.5.pdf) — von Ahn (ESP Game, Verbosity), Phrase Detectives, Wormingo, Ambiguss | Establishes that play can replace paid annotation and that progression mechanics raise label accuracy. | Annotation is still a **task the player knowingly performs**, not an implicit byproduct of communication. | ★★★☆☆ |
| [LanguageARC (LREC 2020)](https://aclanthology.org/2020.cllrd-1.1/) — Fiumara, Cieri, Wright, Liberman (LDC/UPenn) | Live citizen-science portal with real language games; "Name That Language!" alone collected ~450k judgments. | Not focused on speech **production** at scale, nor on dialect labels derived from listener behaviour. | ★★★☆☆ |
| [GWAP for low-resource dialogue (GamNLP 2020)](https://aclanthology.org/2020.gamnlp-1.7.pdf) — Yusupujiang & Ginzburg (Uyghur) | Same intent as Neva: games to elicit natural dialogue where no corpus exists. | Design paper only — no scale, quality filtering, or training loop. | ★★★☆☆ |
| [HCRC Map Task (LDC93S12)](https://catalog.ldc.upenn.edu/LDC93S12) · Columbia Games Corpus · Spot-the-Difference / DiapixUK | Games used to elicit natural task-oriented speech with a known semantic target. | Not a scalable pipeline — participants are recruited and annotation happens afterwards. | ★★★☆☆ |

### 2. Speech → concept supervision

> **Foundation, not contribution.** Storing **speech → concept** rather than
> **speech → transcript** is the established premise of *visually grounded speech learning*,
> which has operated on this idea since 2015. Neva builds on it and does not claim it as novel.

| Work | What it is | What it doesn't do | Similarity |
|---|---|---|---|
| Visually grounded speech models — [Harwath & Glass (NeurIPS 2016)](https://arxiv.org/abs/1911.09602), [FaST-VGS](https://arxiv.org/pdf/2203.15081), [SpeechCLIP (2022)](https://arxiv.org/pdf/2210.00705) | Models associate raw waveforms with semantically related images **with no transcriptions at all**, recovering word-like units. | Concept pairings come from captioning datasets, **not from humans playing a game**. | ★★★★☆ |
| Vision as an interlingua / low-resource grounding — Harwath, Chuang & Glass (ICASSP 2018); [Kamper et al., Yorùbá few-shot (TASLP 2024)](https://dl.acm.org/doi/10.1109/TASLP.2024.3393772) | Already demonstrates concept-grounded speech learning in a genuinely low-resource language. | Treats a language as **monolithic** — no dialect dimension. | ★★★☆☆ |

### 3. Indian speech corpora at scale — the incumbent

| Work | What it is | What it doesn't do | Similarity |
|---|---|---|---|
| [IndicVoices / Kathbath / Karya](https://arxiv.org/abs/2403.01926) — AI4Bharat, IIT Madras, Sarvam AI | 12,000 hrs, 22,563 speakers, 208 districts, 22 languages, 76% extempore. Already uses district-specific roleplay prompts (Customer & Shopkeeper), retiring each after *k* conversations. | **Paid microtask** crowdsourcing on Karya; dialect *within* a language is **not** an explicit label. No free comprehension-derived signal. | ★★★★☆ |
| [Mozilla Common Voice](https://commonvoice.mozilla.org/) | Peer validation (two upvotes to accept) already replaces expert review with crowd consensus. | Validation is still an **explicit chore**; retention outside major languages is poor. | ★★★☆☆ |
| GigaSpeech 2 | Automated crawl → pseudo-label → filter → refine. The engineering philosophy for our cleaning half. | **No human in the loop.** Starts from web audio and pseudo-labels, not human semantic validation. | ★★☆☆☆ |

---

## The unclaimed gap

Every system above treats comprehension as a property of **a listener in general**. Neva treats
it as a property of **a listener from somewhere**.

> If a Goalpariya speaker's clue is solved quickly by Goalpariya listeners and slowly by
> Sivasagar listeners, that asymmetry **is** the dialect label — and it also gives a perceptual
> distance matrix between varieties, which no open Assamese resource currently has. The utterance
> gets a free quality label; the speaker gets a free dialect label; the language gets a free
> intelligibility map. No annotator is paid.

To our knowledge, **cross-listener comprehension asymmetry as a dialect signal** has not been
used. That — not "a game," not "speech→concept" — is the contribution.

---

## Known threats (see also `LIMITATIONS.md` if present)

- **Bridge-language confound.** The current cross-language build shares a bridge language, which
  erases the asymmetry H2 depends on. The dialect track must drop the bridge and play
  same-language. This is the single most important design change between the hackathon build and
  the research pilot.
- **Collusion.** Shared-incentive players can game the loop; the dialect design needs asymmetric
  information.
- **Performed vs. natural dialect.** Imposter-style loops yield clean labels but stagey speech;
  charades yield natural speech but weak labels. Pairing the two *is* the methodological argument.
- **Vocabulary skew.** Actable-concept games over-represent concrete nouns.
- **Sociolinguistic care.** Accent contrast maps onto real ethnolinguistic tension in Assam.
  Framing is "who knows their district best," never "who sounds funny."

---

## Target venues

Interspeech **SIGUL** · **LREC** (incl. citizen-linguistics track) · **GamNLP**-style workshops ·
**ComputEL**. Release pilot data **CC-BY-4.0** (matching IndicVoices); the dataset outlives the
paper.

---

*Prototyped at the Google DeepMind Bangalore Hackathon. Neva is not affiliated with or endorsed
by Google or Google DeepMind.*
