"""Named prompt templates for the picture-deck engine.

All GenAI prompt text for image generation, verification, translation, and
decoy selection lives here as module-level constants. Pipeline code must
format these templates rather than inlining prompt strings so mid-event
tuning does not require logic changes.

Templates use ``str.format`` placeholders documented beside each constant.
"""

from __future__ import annotations

# 1.1 NB2 Lite — card image generation
NB2_IMAGE_PROMPT = """\
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
  local surroundings — not generic stock-photo Western settings\
{region_emphasis}\
"""

# 1.2 Gemini — image↔label verification
VERIFY_IMAGE_PROMPT = """\
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
the image — regeneration is cheap.\
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
TRANSLATE_LABELS_PROMPT = """\
Translate each English game label below into the target languages. These
are answer options in a game played by everyday speakers in India — use
the most common, colloquial word a native speaker would actually say, not
formal/literary vocabulary. If multiple words are common, pick the most
widely understood. Keep each translation to 1-3 words.

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
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["id", "labels"],
    },
}
