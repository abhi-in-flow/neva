"""Curated culturally safe Indian concepts for picture-deck generation.

Each concept is a scene-level funny situation players recognize instantly,
has distinct names across Indian languages, and stays visually unambiguous
in a single absurd photograph. Abstract ideas, brands, text-dependent
subjects, and regionally offensive or humiliating items are excluded.

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
        concept_noun: Short head noun or action phrase for the image prompt.
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


# Scene-level funny Assam / Northeast-leaning concepts — culturally respectful.
CONCEPTS: tuple[Concept, ...] = (
    Concept(
        id="pink_elephant",
        concept_phrase=(
            "a bright pink elephant calmly sipping tea from a tiny clay kulhad "
            "at a roadside Assamese tea stall"
        ),
        concept_noun="pink elephant",
        labels={
            "en": "pink elephant",
            "hi": "गुलाबी हाथी",
            "as": "গোলাপী হাতী",
            "bn": "গোলাপি হাতি",
        },
        category="animal",
    ),
    Concept(
        id="goat_on_bicycle",
        concept_phrase=(
            "a village goat perched proudly on an old bicycle leaning against "
            "a mud wall in a Northeast Indian courtyard"
        ),
        concept_noun="goat on bicycle",
        labels={
            "en": "goat on bicycle",
            "hi": "साइकिल पर बकरी",
            "as": "চাইকেলত ছাগলী",
            "bn": "সাইকেলে ছাগল",
        },
        category="animal",
    ),
    Concept(
        id="cow_in_gamosa",
        concept_phrase=(
            "a calm cow draped in a red-bordered Assamese gamosa like a scarf, "
            "standing in a village courtyard"
        ),
        concept_noun="cow in gamosa",
        labels={
            "en": "cow in gamosa",
            "hi": "गमोसा वाली गाय",
            "as": "গামোচা পিন্ধা গাই",
            "bn": "গামছা পরা গরু",
        },
        category="animal",
    ),
    Concept(
        id="crow_stealing_jalebi",
        concept_phrase=(
            "a crow mid-snatch lifting orange jalebi from a steel plate at an "
            "outdoor sweet stall"
        ),
        concept_noun="crow stealing jalebi",
        labels={
            "en": "crow stealing jalebi",
            "hi": "जलेबी चुराता कौआ",
            "as": "জিলিপি চোৰোৱা কাউৰী",
            "bn": "জিলিপি চুরি করা কাক",
        },
        category="animal",
    ),
    Concept(
        id="peacock_on_handcart",
        concept_phrase=(
            "an Indian peacock perched atop a wooden handcart piled with "
            "vegetables in a market lane"
        ),
        concept_noun="peacock on handcart",
        labels={
            "en": "peacock on handcart",
            "hi": "ठेले पर मोर",
            "as": "ঠেলাত ময়ূৰ",
            "bn": "ঠেলায় ময়ূর",
        },
        category="animal",
    ),
    Concept(
        id="duck_in_jaapi",
        concept_phrase=(
            "a duck wearing an oversized Assamese jaapi hat beside a village "
            "pond edged with bamboo"
        ),
        concept_noun="duck in jaapi",
        labels={
            "en": "duck in jaapi",
            "hi": "जापी पहना बतख",
            "as": "জাপি পিন্ধা হাঁহ",
            "bn": "জাপি পরা হাঁস",
        },
        category="animal",
    ),
    Concept(
        id="buffalo_in_boat",
        concept_phrase=(
            "a water buffalo sitting politely in a wooden river boat as if "
            "waiting for a ferry ride on a Northeast riverside"
        ),
        concept_noun="buffalo in boat",
        labels={
            "en": "buffalo in boat",
            "hi": "नाव में भैंस",
            "as": "নাৱত ম'হ",
            "bn": "নৌকায় মহিষ",
        },
        category="animal",
    ),
    Concept(
        id="frog_on_dhol",
        concept_phrase=(
            "a small frog sitting like a drummer on an upright Assamese Bihu "
            "dhol against a bamboo wall"
        ),
        concept_noun="frog on dhol",
        labels={
            "en": "frog on dhol",
            "hi": "ढोल पर मेंढक",
            "as": "ঢোলত ভেকুলী",
            "bn": "ঢোলে ব্যাঙ",
        },
        category="animal",
    ),
    Concept(
        id="monkey_with_kalash",
        concept_phrase=(
            "a monkey carefully balancing a brass kalash water pot on its head "
            "near a village well"
        ),
        concept_noun="monkey with water pot",
        labels={
            "en": "monkey with water pot",
            "hi": "कलश वाला बंदर",
            "as": "কলহ লৈ থকা বান্দৰ",
            "bn": "কলস নিয়ে বানর",
        },
        category="animal",
    ),
    Concept(
        id="rooster_on_hand_pump",
        concept_phrase=(
            "a rooster standing proudly on the handle of a village hand pump "
            "as if announcing the morning"
        ),
        concept_noun="rooster on hand pump",
        labels={
            "en": "rooster on hand pump",
            "hi": "हैंड पंप पर मुर्गा",
            "as": "নলকুপত কুকুৰা",
            "bn": "কলে মোরগ",
        },
        category="animal",
    ),
    Concept(
        id="cat_in_loom",
        concept_phrase=(
            "a cat tangled comically in colorful threads of a traditional "
            "Assamese handloom"
        ),
        concept_noun="cat in handloom",
        labels={
            "en": "cat in handloom",
            "hi": "हथकरघे में बिल्ली",
            "as": "তাঁতত মেকুৰী",
            "bn": "তাঁতে বিড়াল",
        },
        category="animal",
    ),
    Concept(
        id="goat_eating_banana_leaf_meal",
        concept_phrase=(
            "a goat politely eating from a banana-leaf Assamese rice meal laid "
            "out on a woven mat"
        ),
        concept_noun="goat eating banana-leaf meal",
        labels={
            "en": "goat eating meal",
            "hi": "केला पत्ते पर खाने वाली बकरी",
            "as": "কলপাতৰ ভাত খোৱা ছাগলী",
            "bn": "কলার পাতায় খাওয়া ছাগল",
        },
        category="animal",
    ),
    Concept(
        id="crow_on_lantern",
        concept_phrase=(
            "a crow balanced on a glowing kerosene lantern at dusk outside a "
            "thatched Assamese house"
        ),
        concept_noun="crow on lantern",
        labels={
            "en": "crow on lantern",
            "hi": "लालटेन पर कौआ",
            "as": "লণ্ঠনত কাউৰী",
            "bn": "লণ্ঠনে কাক",
        },
        category="animal",
    ),
    Concept(
        id="tortoise_in_slippers",
        concept_phrase=(
            "a tortoise wearing oversized rubber slippers on a dusty village "
            "path after monsoon rain"
        ),
        concept_noun="tortoise in slippers",
        labels={
            "en": "tortoise in slippers",
            "hi": "चप्पल पहना कछुआ",
            "as": "চেণ্ডেল পিন্ধা কাছ",
            "bn": "চপ্পল পরা কচ্ছপ",
        },
        category="animal",
    ),
    Concept(
        id="fish_under_umbrella",
        concept_phrase=(
            "fresh river fish oddly sheltered under an open black umbrella "
            "beside a rainy Assamese market stall"
        ),
        concept_noun="fish under umbrella",
        labels={
            "en": "fish under umbrella",
            "hi": "छाते के नीचे मछली",
            "as": "ছাটিৰ তলত মাছ",
            "bn": "ছাতার নিচে মাছ",
        },
        category="animal",
    ),
    Concept(
        id="peacock_in_rickshaw",
        concept_phrase=(
            "a peacock riding as a passenger in a hand-pulled rickshaw on a "
            "quiet town street"
        ),
        concept_noun="peacock in rickshaw",
        labels={
            "en": "peacock in rickshaw",
            "hi": "रिक्शा में मोर",
            "as": "ৰিক্সাত ময়ূৰ",
            "bn": "রিকশায় ময়ূর",
        },
        category="animal",
    ),
    Concept(
        id="monkey_rolling_roti",
        concept_phrase=(
            "a monkey using a wooden belan rolling pin on a flour-dusted board "
            "beside a clay stove"
        ),
        concept_noun="monkey rolling dough",
        labels={
            "en": "monkey rolling dough",
            "hi": "रोटी बेलता बंदर",
            "as": "ৰুটি বেলা বান্দৰ",
            "bn": "রুটি বেলা বানর",
        },
        category="kitchen",
    ),
    Concept(
        id="cow_at_potter_wheel",
        concept_phrase=(
            "a curious cow watching a spinning potter's wheel with a half-shaped "
            "clay pot in a village workshop"
        ),
        concept_noun="cow at potter's wheel",
        labels={
            "en": "cow at potter's wheel",
            "hi": "चाक के पास गाय",
            "as": "কুমাৰৰ চক্ৰৰ ওচৰত গাই",
            "bn": "কুমোরের চাকার কাছে গরু",
        },
        category="farm",
    ),
    Concept(
        id="duck_on_charpai",
        concept_phrase=(
            "a duck nestled like a guest on a woven rope charpai in a sunny "
            "courtyard"
        ),
        concept_noun="duck on rope cot",
        labels={
            "en": "duck on rope cot",
            "hi": "चारपाई पर बतख",
            "as": "খাটত হাঁহ",
            "bn": "খাটে হাঁস",
        },
        category="household",
    ),
    Concept(
        id="goat_at_water_pump",
        concept_phrase=(
            "a goat pressing the handle of a village hand pump with its hoof "
            "while water splashes into a brass pot"
        ),
        concept_noun="goat at hand pump",
        labels={
            "en": "goat at hand pump",
            "hi": "हैंड पंप पर बकरी",
            "as": "নলকুপত ছাগলী",
            "bn": "কলে ছাগল",
        },
        category="household",
    ),
    Concept(
        id="crow_on_temple_bell",
        concept_phrase=(
            "a crow perched on a brass temple bell hanging from a wooden beam, "
            "as if about to ring it"
        ),
        concept_noun="crow on temple bell",
        labels={
            "en": "crow on temple bell",
            "hi": "घंटी पर कौआ",
            "as": "ঘণ্টাত কাউৰী",
            "bn": "ঘণ্টায় কাক",
        },
        category="household",
    ),
    Concept(
        id="buffalo_with_jaapi",
        concept_phrase=(
            "a water buffalo wearing a conical Assamese jaapi hat in a green "
            "paddy field"
        ),
        concept_noun="buffalo with jaapi",
        labels={
            "en": "buffalo with jaapi",
            "hi": "जापी पहनी भैंस",
            "as": "জাপি পিন্ধা ম'হ",
            "bn": "জাপি পরা মহিষ",
        },
        category="animal",
    ),
    Concept(
        id="monkey_with_oil_lamp",
        concept_phrase=(
            "a monkey carefully holding a lit clay diya oil lamp near a dusk "
            "courtyard doorway"
        ),
        concept_noun="monkey with oil lamp",
        labels={
            "en": "monkey with oil lamp",
            "hi": "दीया लिए बंदर",
            "as": "চাকি লৈ থকা বান্দৰ",
            "bn": "প্রদীপ নিয়ে বানর",
        },
        category="household",
    ),
    Concept(
        id="peacock_under_banyan",
        concept_phrase=(
            "a peacock sheltering under aerial roots of a giant banyan while "
            "holding an open umbrella in its beak"
        ),
        concept_noun="peacock with umbrella",
        labels={
            "en": "peacock with umbrella",
            "hi": "छाता लिए मोर",
            "as": "ছাটি লৈ থকা ময়ূৰ",
            "bn": "ছাতা নিয়ে ময়ূর",
        },
        category="nature",
    ),
    Concept(
        id="frog_in_kulhad",
        concept_phrase=(
            "a tiny frog sitting inside a clay kulhad tea cup on a wooden tea "
            "stall bench"
        ),
        concept_noun="frog in clay cup",
        labels={
            "en": "frog in clay cup",
            "hi": "कुल्हड़ में मेंढक",
            "as": "মাটিৰ কাপত ভেকুলী",
            "bn": "মাটির কাপে ব্যাঙ",
        },
        category="kitchen",
    ),
    Concept(
        id="goat_pulling_rickshaw",
        concept_phrase=(
            "a determined goat appearing to pull an empty hand-pulled rickshaw "
            "down a quiet lane"
        ),
        concept_noun="goat pulling rickshaw",
        labels={
            "en": "goat pulling rickshaw",
            "hi": "रिक्शा खींचती बकरी",
            "as": "ৰিক্সা টনা ছাগলী",
            "bn": "রিকশা টানা ছাগল",
        },
        category="transport",
    ),
    Concept(
        id="cat_on_sewing_machine",
        concept_phrase=(
            "a cat lounging on a black manual sewing machine as if supervising "
            "the tailor's table"
        ),
        concept_noun="cat on sewing machine",
        labels={
            "en": "cat on sewing machine",
            "hi": "सिलाई मशीन पर बिल्ली",
            "as": "চিলাই মেচিনত মেকুৰী",
            "bn": "সেলাই মেশিনে বিড়াল",
        },
        category="household",
    ),
    Concept(
        id="crow_with_gamosa_cape",
        concept_phrase=(
            "a crow wearing a small gamosa draped like a superhero cape on a "
            "clay rooftop edge"
        ),
        concept_noun="crow with gamosa cape",
        labels={
            "en": "crow with gamosa",
            "hi": "गमोसा वाला कौआ",
            "as": "গামোচা পিন্ধা কাউৰী",
            "bn": "গামছা পরা কাক",
        },
        category="animal",
    ),
    Concept(
        id="duck_rowing_boat",
        concept_phrase=(
            "a duck standing in a wooden river boat holding a tiny paddle as if "
            "rowing across muddy water"
        ),
        concept_noun="duck rowing boat",
        labels={
            "en": "duck rowing boat",
            "hi": "नाव खेता बतख",
            "as": "নাও বাওৱা হাঁহ",
            "bn": "নৌকা বাইছে হাঁস",
        },
        category="transport",
    ),
    Concept(
        id="monkey_on_sugarcane",
        concept_phrase=(
            "a monkey sitting atop a tall stack of cut sugarcane at a roadside "
            "stall, looking mischievous"
        ),
        concept_noun="monkey on sugarcane",
        labels={
            "en": "monkey on sugarcane",
            "hi": "गन्ने पर बंदर",
            "as": "কুঁহিয়াৰত বান্দৰ",
            "bn": "আখে বানর",
        },
        category="food",
    ),
    Concept(
        id="rooster_chasing_handcart",
        concept_phrase=(
            "a rooster mid-stride chasing a wooden vegetable handcart down a "
            "dusty market lane"
        ),
        concept_noun="rooster chasing handcart",
        labels={
            "en": "rooster chasing handcart",
            "hi": "ठेला पीछे मुर्गा",
            "as": "ঠেলা খেদা কুকুৰা",
            "bn": "ঠেলা তাড়া মোরগ",
        },
        category="transport",
    ),
    Concept(
        id="frog_with_sickle",
        concept_phrase=(
            "a frog sitting beside a curved farming sickle on harvested grain, "
            "as if resting after fieldwork"
        ),
        concept_noun="frog with sickle",
        labels={
            "en": "frog with sickle",
            "hi": "हंसिया वाला मेंढक",
            "as": "কাচি লৈ থকা ভেকুলী",
            "bn": "কাস্তে নিয়ে ব্যাঙ",
        },
        category="farm",
    ),
    Concept(
        id="cat_in_mortar",
        concept_phrase=(
            "a cat peeking out of a large stone spice mortar next to a wooden "
            "pestle in a kitchen courtyard"
        ),
        concept_noun="cat in mortar",
        labels={
            "en": "cat in mortar",
            "hi": "ओखली में बिल्ली",
            "as": "খুন্দনাত মেকুৰী",
            "bn": "হামানদিস্তায় বিড়াল",
        },
        category="kitchen",
    ),
    Concept(
        id="peacock_fanning_jalebi",
        concept_phrase=(
            "a peacock partially fanning its feathers over a plate of fresh "
            "orange jalebi as if cooling the sweets"
        ),
        concept_noun="peacock fanning jalebi",
        labels={
            "en": "peacock fanning jalebi",
            "hi": "जलेबी पर मोर",
            "as": "জিলিপিৰ ওপৰত ময়ূৰ",
            "bn": "জিলিপির উপর ময়ূর",
        },
        category="food",
    ),
    Concept(
        id="buffalo_under_lotus",
        concept_phrase=(
            "a water buffalo in a pond with a pink lotus flower comically "
            "resting on its head"
        ),
        concept_noun="buffalo with lotus",
        labels={
            "en": "buffalo with lotus",
            "hi": "कमल वाली भैंस",
            "as": "পদুম লৈ থকা ম'হ",
            "bn": "পদ্ম নিয়ে মহিষ",
        },
        category="nature",
    ),
    Concept(
        id="pink_elephant_on_bridge",
        concept_phrase=(
            "a bright pink elephant carefully crossing a narrow bamboo footbridge "
            "over a green Northeast stream"
        ),
        concept_noun="pink elephant on bridge",
        labels={
            "en": "pink elephant on bridge",
            "hi": "पुल पर गुलाबी हाथी",
            "as": "দলঙত গোলাপী হাতী",
            "bn": "সাঁকোয় গোলাপি হাতি",
        },
        category="animal",
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
