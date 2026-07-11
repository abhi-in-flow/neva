"""Curated culturally safe Indian concepts for picture-deck generation.

Each concept is an everyday noun or simple scene that players recognize
instantly, has distinct names across Indian languages, and is visually
unambiguous in a single-subject photograph. Abstract ideas, brands,
text-dependent subjects, and regionally offensive or ambiguous items are
excluded.

Architectural boundary: this module is data only. Pipeline code selects
concepts and never invents labels outside this curated pool (plus batched
translation of the English seed labels).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from collections.abc import Mapping, Sequence

from deckgen.config import (
    OPERATOR_CONCEPT_ID_PATTERN,
    OPERATOR_CONCEPT_MAX_TEXT_LENGTH,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Concept:
    """One curated deck concept with English seed labels and prompt nouns.

    Attributes:
        id: Stable concept identifier used in decoy selection JSON.
        concept_phrase: Full phrase substituted into the NB2 image prompt.
        concept_noun: Short head noun for the image prompt.
        labels: Multilingual label map; ``en`` is required. Other languages
            may be pre-seeded or filled by the translation step.
        category: Broad semantic category used for logging and decoy hints.
        locale: Optional per-concept cultural context overriding deck region.
        cultural_hint: Optional operator-provided visual/cultural description.
    """

    id: str
    concept_phrase: str
    concept_noun: str
    labels: Mapping[str, str]
    category: str = "general"
    locale: str | None = None
    cultural_hint: str | None = None


# Everyday Indian domestic / rural / market concepts — culturally safe.
CONCEPTS: tuple[Concept, ...] = (
    Concept(
        id="kalash",
        concept_phrase="a brass water pot (kalash)",
        concept_noun="water pot",
        labels={"en": "water pot", "hi": "कलश", "as": "কলহ", "bn": "কলস"},
        category="kitchen",
    ),
    Concept(
        id="kulhad",
        concept_phrase="a clay tea cup (kulhad) on a wooden bench",
        concept_noun="clay cup",
        labels={"en": "clay cup", "hi": "कुल्हड़", "as": "মাটিৰ কাপ", "bn": "মাটির কাপ"},
        category="kitchen",
    ),
    Concept(
        id="tawa",
        concept_phrase="a flat iron griddle (tawa) on a clay stove",
        concept_noun="griddle",
        labels={"en": "griddle", "hi": "तवा", "as": "তাৱা", "bn": "তাওয়া"},
        category="kitchen",
    ),
    Concept(
        id="pressure_cooker",
        concept_phrase="a stainless steel pressure cooker on a stove",
        concept_noun="pressure cooker",
        labels={"en": "pressure cooker", "hi": "प्रेशर कुकर", "as": "প্ৰেছাৰ কুকাৰ", "bn": "প্রেশার কুকার"},
        category="kitchen",
    ),
    Concept(
        id="rolling_pin",
        concept_phrase="a wooden rolling pin (belan) on a flour-dusted board",
        concept_noun="rolling pin",
        labels={"en": "rolling pin", "hi": "बेलन", "as": "বেলন", "bn": "বেলন"},
        category="kitchen",
    ),
    Concept(
        id="banana_leaf",
        concept_phrase="a fresh green banana leaf plate with simple food",
        concept_noun="banana leaf",
        labels={"en": "banana leaf", "hi": "केले का पत्ता", "as": "কলপাত", "bn": "কলার পাতা"},
        category="food",
    ),
    Concept(
        id="jackfruit",
        concept_phrase="jackfruit hanging on a tree",
        concept_noun="jackfruit",
        labels={"en": "jackfruit", "hi": "कटहल", "as": "কঁঠাল", "bn": "কাঁঠাল"},
        category="food",
    ),
    Concept(
        id="mango",
        concept_phrase="a ripe mango on a woven basket",
        concept_noun="mango",
        labels={"en": "mango", "hi": "आम", "as": "আম", "bn": "আম"},
        category="food",
    ),
    Concept(
        id="coconut",
        concept_phrase="a whole green coconut with a straw hole",
        concept_noun="coconut",
        labels={"en": "coconut", "hi": "नारियल", "as": "নাৰিকল", "bn": "নারকেল"},
        category="food",
    ),
    Concept(
        id="jalebi",
        concept_phrase="a plate of fresh orange jalebi sweets",
        concept_noun="jalebi",
        labels={"en": "jalebi", "hi": "जलेबी", "as": "জিলিপি", "bn": "জিলিপি"},
        category="food",
    ),
    Concept(
        id="idli",
        concept_phrase="steamed idli cakes on a steel plate with chutney",
        concept_noun="idli",
        labels={"en": "idli", "hi": "इडली", "as": "ইডলি", "bn": "ইডলি"},
        category="food",
    ),
    Concept(
        id="samosa",
        concept_phrase="two golden samosas on a paper plate",
        concept_noun="samosa",
        labels={"en": "samosa", "hi": "समोसा", "as": "চিংৰা", "bn": "সিংগাড়া"},
        category="food",
    ),
    Concept(
        id="cow",
        concept_phrase="a calm Indian cow standing near a village path",
        concept_noun="cow",
        labels={"en": "cow", "hi": "गाय", "as": "গাই", "bn": "গরু"},
        category="animal",
    ),
    Concept(
        id="goat",
        concept_phrase="a goat standing in a dusty village courtyard",
        concept_noun="goat",
        labels={"en": "goat", "hi": "बकरी", "as": "ছাগলী", "bn": "ছাগল"},
        category="animal",
    ),
    Concept(
        id="peacock",
        concept_phrase="an Indian peacock with its feathers partially fanned",
        concept_noun="peacock",
        labels={"en": "peacock", "hi": "मोर", "as": "ময়ূৰ", "bn": "ময়ূর"},
        category="animal",
    ),
    Concept(
        id="crow",
        concept_phrase="a crow perched on a clay rooftop edge",
        concept_noun="crow",
        labels={"en": "crow", "hi": "कौआ", "as": "কাউৰী", "bn": "কাক"},
        category="animal",
    ),
    Concept(
        id="fish",
        concept_phrase="fresh river fish laid on a market banana leaf",
        concept_noun="fish",
        labels={"en": "fish", "hi": "मछली", "as": "মাছ", "bn": "মাছ"},
        category="animal",
    ),
    Concept(
        id="bamboo_trap",
        concept_phrase="a bamboo fish trap beside a shallow stream",
        concept_noun="fish trap",
        labels={"en": "fish trap", "hi": "मछली जाल", "as": "জাকৈ", "bn": "চাই"},
        category="farm",
    ),
    Concept(
        id="sickle",
        concept_phrase="a curved farming sickle resting on harvested grain",
        concept_noun="sickle",
        labels={"en": "sickle", "hi": "हंसिया", "as": "কাচি", "bn": "কাস্তে"},
        category="farm",
    ),
    Concept(
        id="plough",
        concept_phrase="a wooden plough in a muddy field",
        concept_noun="plough",
        labels={"en": "plough", "hi": "हल", "as": "নাঙল", "bn": "লাঙ্গল"},
        category="farm",
    ),
    Concept(
        id="handcart",
        concept_phrase="a wooden handcart loaded with vegetables",
        concept_noun="handcart",
        labels={"en": "handcart", "hi": "ठेला", "as": "ঠেলা", "bn": "ঠেলা"},
        category="transport",
    ),
    Concept(
        id="rickshaw",
        concept_phrase="a hand-pulled rickshaw on a quiet street",
        concept_noun="rickshaw",
        labels={"en": "rickshaw", "hi": "रिक्शा", "as": "ৰিক্সা", "bn": "রিকশা"},
        category="transport",
    ),
    Concept(
        id="bicycle",
        concept_phrase="an old bicycle leaning against a mud wall",
        concept_noun="bicycle",
        labels={"en": "bicycle", "hi": "साइकिल", "as": "চাইকেল", "bn": "সাইকেল"},
        category="transport",
    ),
    Concept(
        id="boat",
        concept_phrase="a wooden river boat tied at a muddy bank",
        concept_noun="boat",
        labels={"en": "boat", "hi": "नाव", "as": "নাও", "bn": "নৌকা"},
        category="transport",
    ),
    Concept(
        id="gamosa",
        concept_phrase="a gamosa cloth draped on a wooden chair",
        concept_noun="cloth",
        labels={"en": "gamosa cloth", "hi": "गमोसा", "as": "গামোচা", "bn": "গামছা"},
        category="clothing",
    ),
    Concept(
        id="umbrella",
        concept_phrase="an open black umbrella standing in monsoon rain",
        concept_noun="umbrella",
        labels={"en": "umbrella", "hi": "छाता", "as": "ছাটি", "bn": "ছাতা"},
        category="clothing",
    ),
    Concept(
        id="slippers",
        concept_phrase="a pair of rubber slippers outside a doorway",
        concept_noun="slippers",
        labels={"en": "slippers", "hi": "चप्पल", "as": "চেণ্ডেল", "bn": "চপ্পল"},
        category="clothing",
    ),
    Concept(
        id="turmeric",
        concept_phrase="a small brass bowl of bright yellow turmeric powder",
        concept_noun="turmeric",
        labels={"en": "turmeric", "hi": "हल्दी", "as": "হালধি", "bn": "হলুদ"},
        category="kitchen",
    ),
    Concept(
        id="lantern",
        concept_phrase="an old kerosene lantern glowing at dusk",
        concept_noun="lantern",
        labels={"en": "lantern", "hi": "लालटेन", "as": "লণ্ঠন", "bn": "লণ্ঠন"},
        category="household",
    ),
    Concept(
        id="charpai",
        concept_phrase="a woven rope cot (charpai) in a courtyard",
        concept_noun="cot",
        labels={"en": "rope cot", "hi": "चारपाई", "as": "খাট", "bn": "খাট"},
        category="household",
    ),
    Concept(
        id="mortar_pestle",
        concept_phrase="a stone mortar and pestle with crushed spices",
        concept_noun="mortar",
        labels={"en": "mortar and pestle", "hi": "ओखली मूसल", "as": "খুন্দনা", "bn": "হামানদিস্তা"},
        category="kitchen",
    ),
    Concept(
        id="earthen_stove",
        concept_phrase="a traditional earthen cooking stove with firewood",
        concept_noun="stove",
        labels={"en": "earthen stove", "hi": "चूल्हा", "as": "চুলা", "bn": "উনুন"},
        category="kitchen",
    ),
    Concept(
        id="water_pump",
        concept_phrase="a hand-operated village water pump",
        concept_noun="water pump",
        labels={"en": "hand pump", "hi": "हैंड पंप", "as": "নলকুপ", "bn": "কল"},
        category="household",
    ),
    Concept(
        id="banyan",
        concept_phrase="aerial roots of a large banyan tree",
        concept_noun="banyan tree",
        labels={"en": "banyan tree", "hi": "बरगद", "as": "বটগছ", "bn": "বটগাছ"},
        category="nature",
    ),
    Concept(
        id="lotus",
        concept_phrase="a pink lotus flower floating on a pond",
        concept_noun="lotus",
        labels={"en": "lotus", "hi": "कमल", "as": "পদুম", "bn": "পদ্ম"},
        category="nature",
    ),
    Concept(
        id="rain_cloud",
        concept_phrase="dark monsoon clouds over green fields",
        concept_noun="monsoon clouds",
        labels={"en": "monsoon clouds", "hi": "मानसून के बादल", "as": "বৰষুণৰ ডাৱৰ", "bn": "বর্ষার মেঘ"},
        category="weather",
    ),
    Concept(
        id="well",
        concept_phrase="a circular village well with a stone rim",
        concept_noun="well",
        labels={"en": "well", "hi": "कुआँ", "as": "নাদ", "bn": "কুয়ো"},
        category="household",
    ),
    Concept(
        id="temple_bell",
        concept_phrase="a brass temple bell hanging from a wooden beam",
        concept_noun="temple bell",
        labels={"en": "temple bell", "hi": "घंटी", "as": "ঘণ্টা", "bn": "ঘণ্টা"},
        category="household",
    ),
    Concept(
        id="dhol",
        concept_phrase="a traditional dhol drum resting upright",
        concept_noun="drum",
        labels={"en": "dhol drum", "hi": "ढोल", "as": "ঢোল", "bn": "ঢোল"},
        category="household",
    ),
    Concept(
        id="sewing_machine",
        concept_phrase="a black manual sewing machine on a wooden table",
        concept_noun="sewing machine",
        labels={"en": "sewing machine", "hi": "सिलाई मशीन", "as": "চিলাই মেচিন", "bn": "সেলাই মেশিন"},
        category="household",
    ),
    Concept(
        id="oil_lamp",
        concept_phrase="a small clay oil lamp (diya) with a lit wick",
        concept_noun="oil lamp",
        labels={"en": "oil lamp", "hi": "दीया", "as": "চাকি", "bn": "প্রদীপ"},
        category="household",
    ),
    Concept(
        id="sugarcane",
        concept_phrase="cut stalks of sugarcane stacked at a stall",
        concept_noun="sugarcane",
        labels={"en": "sugarcane", "hi": "गन्ना", "as": "কুঁহিয়াৰ", "bn": "আখ"},
        category="food",
    ),
    Concept(
        id="potter_wheel",
        concept_phrase="a clay pot being shaped on a potter's wheel",
        concept_noun="potter's wheel",
        labels={"en": "potter's wheel", "hi": "चाक", "as": "কুমাৰৰ চক্ৰ", "bn": "কুমোরের চাকা"},
        category="farm",
    ),
    Concept(
        id="weaving_loom",
        concept_phrase="a traditional handloom with colorful threads",
        concept_noun="handloom",
        labels={"en": "handloom", "hi": "हथकरघा", "as": "তাঁত", "bn": "তাঁত"},
        category="household",
    ),
)


def concept_by_id() -> dict[str, Concept]:
    """Build an id→concept lookup map.

    Returns:
        Dict keyed by concept ``id``.
    """
    logger.info("concept_by_id called count=%s", len(CONCEPTS))
    return {c.id: c for c in CONCEPTS}


def select_concepts(count: int, *, seed: int | None = None) -> list[Concept]:
    """Select ``count`` distinct curated concepts for a deck.

    Args:
        count: Number of concepts required.
        seed: Optional deterministic seed for reproducible selection.

    Returns:
        A list of ``Concept`` instances of length ``count``.

    Raises:
        ValueError: If ``count`` exceeds the curated pool size.
    """
    import random

    logger.info("select_concepts called count=%s seed=%s", count, seed)
    if count > len(CONCEPTS):
        raise ValueError(f"Requested {count} cards but only {len(CONCEPTS)} concepts are curated")
    pool = list(CONCEPTS)
    rng = random.Random(seed)
    rng.shuffle(pool)
    selected = pool[:count]
    logger.info(
        "select_concepts completed ids=%s",
        [c.id for c in selected],
    )
    return selected


def concepts_from_operator_mappings(
    mappings: Sequence[Mapping[str, object]],
) -> list[Concept]:
    """Validate and convert operator JSON mappings into deck concepts.

    Operator rows must provide non-blank string values for ``concept_id``,
    ``label_en``, ``locale``, and ``cultural_hint``. The English label becomes
    both the visible English label and short prompt noun; the cultural hint is
    used as the full image prompt phrase, while locale supplies per-concept
    cultural context.

    Args:
        mappings: Ordered operator mappings parsed from JSON.

    Returns:
        Validated ``Concept`` instances preserving the input order.

    Raises:
        TypeError: If the input or a row is not the expected mapping shape.
        ValueError: If a required field is blank/invalid, a row has unknown
            fields, or a concept id is duplicated.
    """
    logger.info(
        "concepts_from_operator_mappings called mapping_count=%s",
        len(mappings) if isinstance(mappings, Sequence) else None,
    )
    if isinstance(mappings, (str, bytes)) or not isinstance(mappings, Sequence):
        raise TypeError("operator concepts must be a list of mappings")

    required = {"concept_id", "label_en", "locale", "cultural_hint"}
    concepts: list[Concept] = []
    seen_ids: set[str] = set()
    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, Mapping):
            raise TypeError(f"operator concept at index {index} must be an object")
        unknown = set(mapping) - required
        missing = required - set(mapping)
        if missing:
            raise ValueError(
                f"operator concept at index {index} is missing fields: {sorted(missing)}"
            )
        if unknown:
            raise ValueError(
                f"operator concept at index {index} has unknown fields: {sorted(unknown)}"
            )

        values: dict[str, str] = {}
        for field_name in sorted(required):
            raw = mapping[field_name]
            if not isinstance(raw, str):
                raise TypeError(
                    f"operator concept at index {index} field {field_name!r} must be a string"
                )
            value = raw.strip()
            if not value:
                raise ValueError(
                    f"operator concept at index {index} field {field_name!r} must not be blank"
                )
            if len(value) > OPERATOR_CONCEPT_MAX_TEXT_LENGTH:
                raise ValueError(
                    f"operator concept at index {index} field {field_name!r} "
                    f"exceeds {OPERATOR_CONCEPT_MAX_TEXT_LENGTH} characters"
                )
            values[field_name] = value

        concept_id = values["concept_id"]
        if re.fullmatch(OPERATOR_CONCEPT_ID_PATTERN, concept_id) is None:
            raise ValueError(
                f"operator concept at index {index} has invalid concept_id {concept_id!r}"
            )
        if concept_id in seen_ids:
            raise ValueError(f"duplicate operator concept_id {concept_id!r}")
        seen_ids.add(concept_id)
        concepts.append(
            Concept(
                id=concept_id,
                concept_phrase=values["cultural_hint"],
                concept_noun=values["label_en"],
                labels={"en": values["label_en"]},
                category="operator",
                locale=values["locale"],
                cultural_hint=values["cultural_hint"],
            )
        )

    logger.info(
        "concepts_from_operator_mappings completed concept_ids=%s",
        [concept.id for concept in concepts],
    )
    return concepts


def concepts_from_operator(
    mappings: Sequence[Mapping[str, object]],
) -> list[Concept]:
    """Convert validated admin API mappings into generation concepts.

    This stable adapter name is shared by the FastAPI deck-control gateway.
    The stricter conversion and validation remain centralized in
    ``concepts_from_operator_mappings``.

    Args:
        mappings: Ordered operator concept dictionaries.

    Returns:
        Validated concepts preserving operator order.
    """
    logger.info(
        "concepts_from_operator called mapping_count=%s",
        len(mappings) if isinstance(mappings, Sequence) else None,
    )
    return concepts_from_operator_mappings(mappings)
