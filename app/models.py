"""Central model identifiers; no Gemini model strings outside this module.

Holds the canonical Gemini, Nano Banana, and Gemma identifiers used by the
Dialect Data Factory. Application feature code imports identifiers from here;
the intentionally isolated ``tune`` environment mirrors the Gemma identifier
in its own config and binds the exact value into every artifact manifest.

Architectural boundary:
- This module is constants-only. It does not call APIs or load settings.
- ``app.gemini_client.GeminiClient`` validates outbound Gemini/Nano Banana
  calls against ``GEMINI_MODELS`` from this file.
- Gemma tuning identifiers are listed for the LoRA harness; they are not
  invoked through the shared Gemini client.
"""

from __future__ import annotations

# Gemini 3.5 Flash — gauntlet triage/contamination, deck verification,
# decoy selection, and batched label translation.
GEMINI_FLASH = "gemini-3.5-flash"

# Nano Banana 2 Lite — Track 3 picture-deck image generation.
NANO_BANANA_LITE = "gemini-3.1-flash-lite-image"

# Optional LoRA target (tune/ only; not used by GeminiClient). This exact
# cached 4-bit instruction checkpoint is also bound into approved manifests.
GEMMA_TUNING_MODEL = "unsloth/gemma-4-E4B-it-unsloth-bnb-4bit"

# Models permitted on the shared Gemini HTTP client.
GEMINI_MODELS: frozenset[str] = frozenset({GEMINI_FLASH, NANO_BANANA_LITE})
