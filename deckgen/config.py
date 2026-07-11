"""Centralized configuration for the regional picture-deck engine.

All tunable deckgen values live here: retry budgets, decoy counts, language
lists, pricing assumptions, region context strings, paths, and timeouts.
Feature modules must import from this module rather than embedding magic
numbers. Environment-backed settings (database URL, data directory, API key
presence) are loaded via pydantic-settings without logging secrets.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models import GEMINI_FLASH, NANO_BANANA_LITE

logger = logging.getLogger(__name__)

# --- Model identifiers (imported from app.models; never inlined elsewhere) ---
IMAGE_MODEL = NANO_BANANA_LITE
VERIFY_MODEL = GEMINI_FLASH
TRANSLATE_MODEL = GEMINI_FLASH
DECOY_MODEL = GEMINI_FLASH
CONCEPT_MODEL = GEMINI_FLASH

# --- Generation / verification ---
MAX_IMAGE_RETRIES = 2  # retries after the first attempt (≤ 3 total attempts)
MAX_CONCEPT_GEN_RETRIES = 2  # retries after the first invent attempt
# Prompt-invented concepts are less predictable than curated concepts, so the
# demo path gets five total image/verification attempts before failing safely.
PROMPT_MAX_IMAGE_RETRIES = 4
# Deck orchestration and the shared Gemini client both cap concurrent NB2 calls.
IMAGE_GENERATION_CONCURRENCY = 4
VERIFY_THINKING_LEVEL = "low"
IMAGE_MIME_TYPE = "image/png"
# Native Gemini image option; translated to ImageConfig by the shared client.
IMAGE_ASPECT_RATIO = "1:1"
FAKE_IMAGE_BYTES = b"\x89PNG\r\n\x1a\nFAKE_DECKGEN_IMAGE"

# --- Deck composition ---
DEFAULT_CARD_COUNT = 30
MIN_CARD_COUNT = 6  # must exceed N_DECOYS so each card has enough pool peers
MAX_CARD_COUNT = 60
N_DECOYS = 5
TARGET_LANGUAGES: tuple[str, ...] = ("en", "hi", "as", "bn")
OPERATOR_CONCEPT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
OPERATOR_CONCEPT_MAX_TEXT_LENGTH = 500

# --- Admin prompt-to-deck (primary operator path) ---
PROMPT_DEFAULT_CARD_COUNT = 8
PROMPT_MIN_CARD_COUNT = 6
PROMPT_MAX_CARD_COUNT = 20
PROMPT_MAX_CHARS = 240
PROGRESS_STAGE_INVENTING = "inventing_concepts"
PROGRESS_STAGE_IMAGES = "generating_images"
PROGRESS_STAGE_DECOYS = "finalizing_decoys"
PROGRESS_STAGE_READY = "ready"
PROGRESS_STAGE_FAILED = "failed"

# --- Pricing (USD) used for Track 3 demo metrics ---
# NB2 Lite is ~$0.0336 per image (arch doc correction vs event PDF typo).
COST_PER_IMAGE_USD = 0.0336
# Approximate Gemini Flash text/JSON call cost for translation/verify/decoy.
COST_PER_FLASH_CALL_USD = 0.0004

# --- Region tag → culturally grounded prompt context ---
# Canonical keys are the 28 Indian states (lowercase hyphenated). Legacy
# aliases (bengal, bangalore, north, northeast, tamil, …) remain for CLI and
# older operator JSON payloads.
REGION_CONTEXTS: dict[str, str] = {
    # 28 Indian states
    "andhra-pradesh": "Andhra Pradesh coastal town and temple-town streets",
    "arunachal-pradesh": "Arunachal Pradesh mountain village and hillside paths",
    "assam": "Assamese village and Brahmaputra riverside",
    "bihar": "Bihar village courtyard and riverside ghat",
    "chhattisgarh": "Chhattisgarh forest-edge village and tribal market",
    "goa": "Goan coastal village and spice-market lanes",
    "gujarat": "Gujarati town market and courtyard",
    "haryana": "Haryana village farmyard and dusty market lane",
    "himachal-pradesh": "Himachal Pradesh mountain village and apple-orchard paths",
    "jharkhand": "Jharkhand forest village and tribal market",
    "karnataka": "Karnataka town street and temple-town lanes",
    "kerala": "Kerala coastal town and backwater village",
    "madhya-pradesh": "Madhya Pradesh town market and fort-town streets",
    "maharashtra": "Maharashtra village courtyard and busy town lane",
    "manipur": "Manipur valley town and riverside market",
    "meghalaya": "Meghalaya hill village and misty market path",
    "mizoram": "Mizoram hill town and bamboo-lined village street",
    "nagaland": "Nagaland hill village and market courtyard",
    "odisha": "Odisha coastal town and temple-town street",
    "punjab": "Punjabi village courtyard and mustard-field edge",
    "rajasthan": "Rajasthan desert-town market and courtyard",
    "sikkim": "Sikkim mountain village and monastery-town path",
    "tamil-nadu": "Tamil Nadu temple-town street and coastal village",
    "telangana": "Telangana town market and Deccan village lane",
    "tripura": "Tripura hill-town market and bamboo courtyard",
    "uttar-pradesh": "Uttar Pradesh town ghat and village courtyard",
    "uttarakhand": "Uttarakhand mountain village and hill-town path",
    "west-bengal": "West Bengal town market and riverside lane",
    # Legacy aliases retained for compatibility
    "bengal": "Bengali town market",
    "bangalore": "Bengaluru urban street",
    "north": "North Indian market",
    "northeast": "rural Northeast Indian riverside",
    "tamil": "Tamil Nadu temple-town street",
}

DEFAULT_REGION = "assam"
STRENGTHENED_REGION_SUFFIX = (
    " Emphasize unmistakably local Indian materials, clothing styles, "
    "architecture, and surroundings for {region_context}; avoid any Western "
    "stock-photo look, studio backdrop, or plain catalog framing. Heighten "
    "the visible whimsical absurdity so the gag is obvious at phone size, "
    "while keeping the single target concept instantly guessable and free of "
    "stereotypes or humiliation."
)

# --- Publication ---
DECK_STATUS_DRAFT = "draft"
DECK_STATUS_GENERATING = "generating"
DECK_STATUS_READY = "ready"
DECK_STATUS_LIVE = "live"
DECK_STATUS_FAILED = "failed"
DECK_FINAL_STATUSES: tuple[str, ...] = (DECK_STATUS_READY, DECK_STATUS_LIVE)
MAX_FAILURE_REASON_LENGTH = 1000
RELATIVE_DECKS_DIR = "decks"


class DeckgenSettings(BaseSettings):
    """Environment-backed settings for live (non-dry-run) deck publication.

    Attributes:
        database_url: Postgres DSN used for atomic deck inserts.
        data_dir: Runtime blob root; card PNGs land under ``decks/<id>/``.
        gemini_api_key: Present for live mode; never logged.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://dialect:dialect_dev_only@localhost:5432/dialect_factory"
    data_dir: Path = Path("data")
    gemini_api_key: str = ""


def database_log_meta(database_url: str) -> dict[str, object]:
    """Return non-secret DSN metadata for INFO logs.

    Args:
        database_url: Full Postgres DSN that may contain credentials.

    Returns:
        Dict with scheme, host, port, and database name only.
    """
    logger.info("database_log_meta called url_length=%s", len(database_url))
    parsed = urlparse(database_url)
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "port": parsed.port,
        "database": parsed.path.lstrip("/") or None,
    }


@lru_cache
def get_settings() -> DeckgenSettings:
    """Load and cache deckgen settings for the current process.

    Returns:
        Cached ``DeckgenSettings`` from environment / ``.env``.

    Side effects:
        Reads environment on first call. Logs safe metadata only; never logs
        the API key or raw DSN credentials.
    """
    settings = DeckgenSettings()
    logger.info(
        "get_settings called data_dir=%s has_gemini_api_key=%s database=%s",
        settings.data_dir,
        bool(settings.gemini_api_key),
        database_log_meta(settings.database_url),
    )
    return settings


def resolve_region_context(region: str) -> str:
    """Map a CLI region tag to the culturally grounded prompt context string.

    Args:
        region: Region tag from ``--region`` (case-insensitive).

    Returns:
        A human-readable region context phrase for image prompts.

    Raises:
        ValueError: If the region tag is not in ``REGION_CONTEXTS``.
    """
    key = region.strip().lower()
    logger.info("resolve_region_context called region=%s", key)
    if key not in REGION_CONTEXTS:
        known = ", ".join(sorted(REGION_CONTEXTS))
        raise ValueError(f"Unknown region '{region}'. Known: {known}")
    return REGION_CONTEXTS[key]
