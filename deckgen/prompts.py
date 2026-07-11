"""Named prompt templates for the picture-deck engine.

All GenAI prompt text for image generation, verification, translation, and
decoy selection lives here as module-level constants. Pipeline code must
format these templates rather than inlining prompt strings so mid-event
tuning does not require logic changes.

Templates use ``str.format`` placeholders documented beside each constant.
"""

from __future__ import annotations

# 1.1 NB2 Lite — card image generation
# Placeholders: concept_phrase, concept_noun, region_context, region_emphasis
NB2_IMAGE_PROMPT = """\
Whimsical, visibly absurd comic photograph of {concept_phrase}, set in an
authentic {region_context} scene in India. The target concept "{concept_noun}"
must be unmistakable at a glance on a phone screen: one clear focal gag, not a
studio product shot.

Composition: square mobile-card frame, eye-level or slight wide shot, warm
natural daylight, lived-in regional depth (homes, markets, riverbanks, tea
stalls, courtyards). Supporting props and background activity are welcome when
they enrich the joke, but nothing may compete with the target for attention.

Tone: funny and culturally grounded — the kind of gentle visual absurdity a
local player would laugh at and instantly name (for example a pink elephant in
a village scene, or another unmistakably silly regional situation).

Strict requirements:
- Exactly one unmistakable target concept/action; guessable in about 2 seconds
- Visibly whimsical or absurd humor (not a plain catalog photo of an object)
- Absolutely NO text anywhere: no signage, labels, packaging text, posters,
  banners, watermarks, or writing of any kind
- No brand logos or recognizable commercial marks
- No sharp identifiable human faces (hands, backs, or distant silhouettes only)
- No offensive stereotypes, humiliating depictions of people or cultures,
  cruelty, or unsafe situations
- No studio, white, blank, seamless, or empty backgrounds
- No generic Western stock-photo suburbs, malls, or interiors
- Culturally accurate to {region_context}: local materials, clothing cues,
  architecture, and surroundings\
{region_emphasis}\
"""

# 1.2 Gemini — image↔label verification
# Placeholders: label_en, region_context
VERIFY_IMAGE_PROMPT = """\
You are a strict quality gate for a picture-guessing charades game. Players
will see this image and must recognize it as: "{label_en}".

Evaluate the attached image:

1. depicts_label: Is "{label_en}" immediately and unambiguously recognizable
   as the target concept/action within about 2 seconds on a phone? If a
   reasonable player would name something else first, answer false.
2. has_text: Is there ANY visible text, lettering, signage, or writing
   anywhere in the image?
3. has_ambiguity: Could a reasonable player name this image as a different
   common concept instead? If yes, name the competing interpretation.
4. cultural_ok: Does the scene look like an authentic Indian regional setting
   ({region_context}) rather than Western stock photography, AND is it free of
   offensive stereotypes, humiliating depictions, cruelty, or unsafe framing?
5. Humor gate (feeds verdict only; do not add schema fields): the image must
   show clear visual absurdity or whimsical humor while remaining instantly
   guessable. A plain, non-funny product shot of the object fails even if the
   object is correct.

Set verdict to "pass" only when depicts_label is true, has_text is false,
cultural_ok is true, ambiguity is low enough for fair play, AND the humor gate
passes. Otherwise set verdict to "fail" and explain in reason. Be strict: when
in doubt, fail — regeneration is cheap.

Respond ONLY with JSON matching the schema.\
"""

VERIFY_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "depicts_label": {"type": "boolean"},
        "has_text": {"type": "boolean"},
        "has_ambiguity": {"type": "boolean"},
        "competing_interpretation": {"type": ["string", "null"]},
        "cultural_ok": {"type": "boolean"},
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "reason": {"type": "string"},
    },
    "required": [
        "depicts_label",
        "has_text",
        "has_ambiguity",
        "competing_interpretation",
        "cultural_ok",
        "verdict",
        "reason",
    ],
}

# 1.3 Gemini — decoy selection (batched per deck)
# Placeholders: n_decoys, json_block
DECOY_SELECTION_PROMPT = """\
You are designing wrong-answer options for a picture-guessing game played
in India. For each target concept below, choose {n_decoys} decoys FROM THE
PROVIDED CONCEPT LIST ONLY.

Good decoys are semantically adjacent (same broad category — a player who
half-understood the audio clue might plausibly pick them) but visually and
verbally distinct (no near-synonyms, no items whose name in Hindi, Assamese,
or Bengali is nearly identical to the target's name).

Targets and candidate pool:
{json_block}

Respond ONLY with JSON: [{{"card_id": ..., "decoy_concept_ids": [...]}}]\
"""

DECOY_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "card_id": {"type": "string"},
            "decoy_concept_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["card_id", "decoy_concept_ids"],
    },
}

# 1.4 Gemini — batched label translation
# Placeholders: json_list, lang_list
TRANSLATE_LABELS_PROMPT = """\
Translate each English game label below into the target languages. These
are answer options in a charades-style game played by everyday speakers in
India — use the most common, colloquial wording a native speaker would
actually say, not formal/literary vocabulary. Labels may be short phrases or
actions (not only single nouns). If multiple wordings are common, pick the
most widely understood. Keep each translation concise (about 1-5 words).

Labels: {json_list}
Target languages: {lang_list}

Respond ONLY with JSON: [{{"id": ..., "labels": {{"en": ..., "hi": ..., ...}}}}]\
"""

TRANSLATE_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "labels": {
                "type": "object",
                "properties": {
                    "en": {"type": "string"},
                    "hi": {"type": "string"},
                    "as": {"type": "string"},
                    "bn": {"type": "string"},
                },
                "required": ["en", "hi", "as", "bn"],
            },
        },
        "required": ["id", "labels"],
    },
}
