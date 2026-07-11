"""Focused tests for absurd/scene-level NB2 prompts and curated concepts.

Exercises prompt formatting placeholders, verification schema stability,
curated pool size/content, and the operator demo concepts file without
calling Gemini or mutating runtime data.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from google.genai import types

from deckgen.concepts import CONCEPTS, concept_by_id, select_concepts
from deckgen.config import (
    DEFAULT_CARD_COUNT,
    MIN_CARD_COUNT,
    N_DECOYS,
    STRENGTHENED_REGION_SUFFIX,
)
from deckgen.pipeline import format_image_prompt
from deckgen.prompts import (
    NB2_IMAGE_PROMPT,
    VERIFY_IMAGE_PROMPT,
    VERIFY_RESPONSE_SCHEMA,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_CONCEPTS_PATH = REPO_ROOT / "build-docs" / "demo-deck-concepts.example.json"


def test_nb2_prompt_formats_with_required_placeholders() -> None:
    """NB2 template substitutes all required variables and rejects studio framing."""
    logger.info("test_nb2_prompt_formats_with_required_placeholders")
    formatted = NB2_IMAGE_PROMPT.format(
        concept_phrase="a bright pink elephant sipping tea from a kulhad",
        concept_noun="pink elephant",
        region_context="Assamese village",
        region_emphasis="",
    )
    assert "pink elephant" in formatted
    assert "Assamese village" in formatted
    assert "studio" in formatted.lower() or "product shot" in formatted.lower()
    assert "NO text" in formatted or "no text" in formatted.lower()
    assert "stereotype" in formatted.lower()
    assert "{concept_phrase}" not in formatted
    assert "{region_emphasis}" not in formatted


def test_format_image_prompt_appends_strengthened_region_suffix() -> None:
    """Retry emphasis appends the centralized strengthened region/humor clause."""
    logger.info("test_format_image_prompt_appends_strengthened_region_suffix")
    concept = concept_by_id()["pink_elephant"]
    plain = format_image_prompt(concept, "Assamese village", strengthen_region=False)
    strong = format_image_prompt(concept, "Assamese village", strengthen_region=True)
    assert len(strong) > len(plain)
    assert "whimsical absurdity" in strong or "absurdity" in strong.lower()
    assert STRENGTHENED_REGION_SUFFIX.format(region_context="Assamese village") in strong
    assert "pink elephant" in plain.lower()


def test_verify_prompt_checks_humor_and_schema_unchanged() -> None:
    """Verification guidance covers humor/region/safety without schema drift."""
    logger.info("test_verify_prompt_checks_humor_and_schema_unchanged")
    prompt = VERIFY_IMAGE_PROMPT.format(
        label_en="pink elephant",
        region_context="Assamese village",
    )
    lowered = prompt.lower()
    assert "absurdity" in lowered or "humor" in lowered
    assert "stereotype" in lowered
    assert "text" in lowered
    assert "pink elephant" in prompt
    required = {
        "depicts_label",
        "has_text",
        "has_ambiguity",
        "competing_interpretation",
        "cultural_ok",
        "verdict",
        "reason",
    }
    assert set(VERIFY_RESPONSE_SCHEMA["required"]) == required
    assert "visibly_absurd" not in VERIFY_RESPONSE_SCHEMA["properties"]
    nullable_field = VERIFY_RESPONSE_SCHEMA["properties"]["competing_interpretation"]
    resolved_field = types.Schema.model_validate(nullable_field)
    assert resolved_field.type == types.Type.STRING
    assert resolved_field.nullable is True


def test_curated_concepts_are_scene_level_and_deck_sized() -> None:
    """Default curated pool is large enough and includes pink elephant scenes."""
    logger.info("test_curated_concepts_are_scene_level_and_deck_sized")
    assert len(CONCEPTS) >= DEFAULT_CARD_COUNT
    assert len(CONCEPTS) > N_DECOYS
    by_id = concept_by_id()
    assert "pink_elephant" in by_id
    pink = by_id["pink_elephant"]
    assert "pink" in pink.labels["en"].lower()
    assert "elephant" in pink.concept_phrase.lower()
    # Scene phrases should be richer than a lone noun.
    assert any(len(c.concept_phrase.split()) >= 6 for c in CONCEPTS)
    selected = select_concepts(MIN_CARD_COUNT, seed=3)
    assert len(selected) == MIN_CARD_COUNT
    assert len({c.id for c in selected}) == MIN_CARD_COUNT


def test_demo_concepts_file_loads_funny_operator_scenes() -> None:
    """Operator demo JSON remains the normal path for funny Assam scenes."""
    logger.info("test_demo_concepts_file_loads_funny_operator_scenes")
    payload = json.loads(DEMO_CONCEPTS_PATH.read_text(encoding="utf-8"))
    assert payload["region_tag"] == "assam"
    concepts = payload["concepts"]
    assert len(concepts) >= MIN_CARD_COUNT
    assert len(concepts) > N_DECOYS
    labels = {row["label_en"].lower() for row in concepts}
    assert any("pink elephant" in label for label in labels)
    for row in concepts:
        # Hints describe scenes, not bare nouns.
        assert len(row["cultural_hint"].split()) >= 8
        assert " " in row["cultural_hint"].strip()
        for key in ("concept_id", "label_en", "locale", "cultural_hint"):
            assert isinstance(row[key], str) and row[key].strip()
