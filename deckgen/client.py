"""Deck-owned GenAI client protocol, dry-run fake, and shared-client adapter.

The orchestrator owns ``app.gemini_client``. This module never implements a
competing production Gemini wrapper. Instead it:

1. Declares the narrow async ``DeckGenAIClient`` protocol deckgen needs.
2. Provides ``FakeDeckGenAIClient`` for dry-run and unit tests (no network).
3. Provides ``SharedGeminiClientAdapter`` that binds to
   ``app.gemini_client.create_gemini_client`` / ``GeminiClient``.

Shared-client surface (Wave 1, verified against ``app/gemini_client.py``):

- Factory: ``create_gemini_client()`` → ``GeminiClient``
- Images: ``await generate_images(model=, operation=, prompt=)`` → ``ImageResult``
  with ``images: tuple[MediaBlob, ...]`` (use ``images[0].data``)
- JSON object: ``await generate_json(model=, operation=, contents=,
  response_schema=, thinking_level=)`` → ``dict``
- JSON array (translate/decoy): ``await generate_content(... response_schema=)``
  then read ``parsed`` or ``json.loads(text)`` — ``generate_json`` rejects
  non-object JSON.

Mismatch notes for the orchestrator:
- Deckgen's protocol is a thin async façade; live mode requires the shared
  async client (no sync twin).
- List-shaped structured outputs must go through ``generate_content``, not
  ``generate_json`` (which enforces ``dict``).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from deckgen.config import FAKE_IMAGE_BYTES, IMAGE_ASPECT_RATIO, IMAGE_MIME_TYPE

logger = logging.getLogger(__name__)

SHARED_CLIENT_EXPECTATIONS = """
app.gemini_client (async) — verified Wave 1 surface:

  create_gemini_client() -> GeminiClient

  await client.generate_images(
      model: str, operation: str, prompt: str
  ) -> ImageResult  # .images[i].data / .mime_type

  await client.generate_json(
      model: str, operation: str,
      contents: str | MediaBlob | Sequence,
      response_schema: Any,
      thinking_level: str = "low",
  ) -> dict

  await client.generate_content(
      model: str, operation: str,
      contents: str | MediaBlob | Sequence,
      response_schema: Any | None = None,
      thinking_level: str | None = None,
  ) -> ContentResult  # .text / .parsed  (use for JSON arrays)

Media: app.gemini_client.MediaBlob(data: bytes, mime_type: str)
"""


@dataclass(frozen=True)
class GeneratedImage:
    """Image bytes plus safe metadata for logging (no inline payload).

    Attributes:
        data: Raw image bytes (PNG in production and dry-run).
        mime_type: MIME type string.
        byte_length: Length of ``data`` for safe INFO logs.
        sha256_hex: Content hash for correlation without logging bytes.
    """

    data: bytes
    mime_type: str
    byte_length: int
    sha256_hex: str


def image_meta(data: bytes, mime_type: str = IMAGE_MIME_TYPE) -> GeneratedImage:
    """Wrap image bytes with hash/length metadata for safe logging.

    Args:
        data: Raw image bytes.
        mime_type: MIME type of the payload.

    Returns:
        A ``GeneratedImage`` with hash and length populated.
    """
    digest = hashlib.sha256(data).hexdigest()
    logger.info(
        "image_meta called mime_type=%s byte_length=%s sha256_hex=%s",
        mime_type,
        len(data),
        digest,
    )
    return GeneratedImage(
        data=data,
        mime_type=mime_type,
        byte_length=len(data),
        sha256_hex=digest,
    )


@runtime_checkable
class DeckGenAIClient(Protocol):
    """Narrow async GenAI surface required by the deck pipeline."""

    async def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        operation: str,
    ) -> GeneratedImage:
        """Generate a single card image from a text prompt."""

    async def generate_json(
        self,
        *,
        model: str,
        prompt: str,
        operation: str,
        response_schema: dict[str, Any],
        thinking_level: str | None = None,
        image_bytes: bytes | None = None,
        image_mime_type: str | None = None,
    ) -> Any:
        """Run a JSON-schema-constrained Gemini call; optional image input."""


class FakeDeckGenAIClient:
    """Deterministic in-memory GenAI stand-in for dry-run and tests.

    Never opens network sockets. Returns fake PNG bytes and scripted JSON
    responses. Call history is recorded for assertions.
    """

    def __init__(
        self,
        *,
        verify_results: list[dict[str, Any]] | None = None,
        translate_result: list[dict[str, Any]] | None = None,
        decoy_result: list[dict[str, Any]] | None = None,
        concept_result: dict[str, Any] | None = None,
    ) -> None:
        """Configure optional scripted responses for pipeline tests.

        Args:
            verify_results: Ordered verification JSON dicts (popped per call).
            translate_result: Batched translation JSON array.
            decoy_result: Batched decoy-selection JSON array.
            concept_result: Scripted invent_concepts object payload.
        """
        logger.info(
            "FakeDeckGenAIClient.__init__ called verify_script_len=%s "
            "has_translate=%s has_decoy=%s has_concepts=%s",
            len(verify_results or []),
            translate_result is not None,
            decoy_result is not None,
            concept_result is not None,
        )
        self.verify_results = list(verify_results or [])
        self.translate_result = translate_result
        self.decoy_result = decoy_result
        self.concept_result = concept_result
        self.calls: list[dict[str, Any]] = []
        self._verify_index = 0

    async def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        operation: str,
    ) -> GeneratedImage:
        """Return fake PNG bytes without calling any API.

        Args:
            model: Model id (logged only).
            prompt: Image prompt text (logged; no secrets).
            operation: Operation label for metrics/logging.

        Returns:
            ``GeneratedImage`` wrapping ``FAKE_IMAGE_BYTES``.
        """
        logger.info(
            "FakeDeckGenAIClient.generate_image model=%s operation=%s "
            "prompt_chars=%s",
            model,
            operation,
            len(prompt),
        )
        self.calls.append(
            {
                "method": "generate_image",
                "model": model,
                "operation": operation,
                "prompt_chars": len(prompt),
            }
        )
        return image_meta(FAKE_IMAGE_BYTES)

    async def generate_json(
        self,
        *,
        model: str,
        prompt: str,
        operation: str,
        response_schema: dict[str, Any],
        thinking_level: str | None = None,
        image_bytes: bytes | None = None,
        image_mime_type: str | None = None,
    ) -> Any:
        """Return scripted or heuristic JSON for verify/translate/decoy.

        Args:
            model: Model id.
            prompt: Prompt text.
            operation: One of ``verify_image``, ``translate_labels``,
                ``select_decoys``, ``invent_concepts``, or a test-specific label.
            response_schema: Expected schema (logged as keys only).
            thinking_level: Optional thinking setting.
            image_bytes: Optional image input (length logged, bytes stripped).
            image_mime_type: MIME type when an image is attached.

        Returns:
            A dict or list matching the requested operation shape.
        """
        image_len = len(image_bytes) if image_bytes is not None else 0
        logger.info(
            "FakeDeckGenAIClient.generate_json model=%s operation=%s "
            "thinking_level=%s prompt_chars=%s image_byte_length=%s "
            "image_mime_type=%s schema_keys=%s",
            model,
            operation,
            thinking_level,
            len(prompt),
            image_len,
            image_mime_type,
            list(response_schema.keys()),
        )
        self.calls.append(
            {
                "method": "generate_json",
                "model": model,
                "operation": operation,
                "thinking_level": thinking_level,
                "prompt_chars": len(prompt),
                "image_byte_length": image_len,
            }
        )
        if operation == "verify_image":
            if self._verify_index < len(self.verify_results):
                result = self.verify_results[self._verify_index]
                self._verify_index += 1
                logger.info(
                    "FakeDeckGenAIClient.generate_json verify scripted verdict=%s",
                    result.get("verdict"),
                )
                return result
            return {
                "depicts_label": True,
                "has_text": False,
                "has_ambiguity": False,
                "competing_interpretation": None,
                "cultural_ok": True,
                "verdict": "pass",
                "reason": "fake pass",
            }
        if operation == "translate_labels":
            if self.translate_result is not None:
                return self.translate_result
            return _fake_translate_from_prompt(prompt)
        if operation == "select_decoys":
            if self.decoy_result is not None:
                return self.decoy_result
            return _fake_decoys_from_prompt(prompt)
        if operation == "invent_concepts":
            if self.concept_result is not None:
                return self.concept_result
            return _fake_concepts_from_prompt(prompt)
        raise ValueError(f"FakeDeckGenAIClient: unknown operation {operation!r}")


def _fake_concepts_from_prompt(prompt: str) -> dict[str, Any]:
    """Build a deterministic invent_concepts payload from the theme prompt.

    Args:
        prompt: Formatted invent prompt containing ``Invent exactly N``.

    Returns:
        Object shaped like ``CONCEPT_FROM_PROMPT_RESPONSE_SCHEMA``.
    """
    logger.info("_fake_concepts_from_prompt called prompt_chars=%s", len(prompt))
    count = 8
    marker = "Invent exactly "
    if marker in prompt:
        try:
            count = int(prompt.split(marker, 1)[1].split(" ", 1)[0])
        except (IndexError, ValueError):
            count = 8
    concepts = [
        {
            "concept_id": f"theme_concept_{index}",
            "label_en": f"Theme concept {index}",
            "locale": "Assamese village courtyard",
            "cultural_hint": (
                f"A playful culturally grounded scene number {index} with one "
                "clear visual gag and no text anywhere"
            ),
        }
        for index in range(count)
    ]
    return {"concepts": concepts}


def _fake_translate_from_prompt(prompt: str) -> list[dict[str, Any]]:
    """Build identity translations from the labels JSON embedded in a prompt.

    Args:
        prompt: Formatted translation prompt containing a labels JSON list.

    Returns:
        List of ``{id, labels}`` dicts with English copied to all targets.
    """
    logger.info("_fake_translate_from_prompt called prompt_chars=%s", len(prompt))
    marker = "Labels: "
    start = prompt.find(marker)
    if start < 0:
        return []
    rest = prompt[start + len(marker) :]
    end = rest.find("\nTarget languages:")
    raw = rest[:end].strip() if end >= 0 else rest.strip()
    labels_list = json.loads(raw)
    out: list[dict[str, Any]] = []
    for item in labels_list:
        en = item.get("en") or item.get("label") or ""
        cid = item.get("id")
        out.append(
            {
                "id": cid,
                "labels": {"en": en, "hi": en, "as": en, "bn": en},
            }
        )
    return out


def _fake_decoys_from_prompt(prompt: str) -> list[dict[str, Any]]:
    """Pick the first N other concept ids as decoys from the prompt JSON block.

    Args:
        prompt: Formatted decoy prompt containing targets and pool JSON.

    Returns:
        List of ``{card_id, decoy_concept_ids}`` entries.
    """
    logger.info("_fake_decoys_from_prompt called prompt_chars=%s", len(prompt))
    marker = "Targets and candidate pool:\n"
    start = prompt.find(marker)
    if start < 0:
        return []
    raw = prompt[start + len(marker) :].split("\n\nRespond")[0].strip()
    block = json.loads(raw)
    if not isinstance(block, dict) or "pool" not in block or "targets" not in block:
        return []
    pool_ids = [p["concept_id"] for p in block["pool"]]
    n = 5
    if "choose " in prompt:
        try:
            n = int(prompt.split("choose ")[1].split(" decoys")[0])
        except (IndexError, ValueError):
            n = 5
    results: list[dict[str, Any]] = []
    for target in block["targets"]:
        card_id = target["card_id"]
        own = target.get("concept_id", card_id)
        decoys = [pid for pid in pool_ids if pid != own][:n]
        results.append({"card_id": card_id, "decoy_concept_ids": decoys})
    return results


class SharedClientMismatchError(RuntimeError):
    """Raised when ``app.gemini_client`` does not match deckgen expectations."""


class SharedGeminiClientAdapter:
    """Adapt orchestrator-owned async ``GeminiClient`` to ``DeckGenAIClient``.

    Maps deckgen's sync-shaped kwargs onto the shared async API:
    ``generate_images``, ``generate_json`` (objects), and ``generate_content``
    (arrays). Does not embed retry/backoff — that stays in the shared client.
    """

    def __init__(self, shared: Any | None = None) -> None:
        """Optionally inject a pre-built ``GeminiClient``.

        Args:
            shared: ``GeminiClient`` instance. When ``None``, created via
                ``create_gemini_client()`` on first use.
        """
        logger.info(
            "SharedGeminiClientAdapter.__init__ called injected=%s",
            shared is not None,
        )
        self._shared = shared

    def _resolve(self) -> Any:
        """Import and return the shared ``GeminiClient``.

        Returns:
            A ``GeminiClient`` instance.

        Raises:
            SharedClientMismatchError: If the module or methods are missing.
        """
        if self._shared is not None:
            return self._shared
        logger.info(
            "SharedGeminiClientAdapter._resolve importing app.gemini_client"
        )
        try:
            from app.gemini_client import create_gemini_client
        except ImportError as exc:
            raise SharedClientMismatchError(
                "app.gemini_client is not available. "
                f"Deckgen expects:\n{SHARED_CLIENT_EXPECTATIONS}"
            ) from exc

        try:
            client = create_gemini_client()
        except Exception as exc:
            raise SharedClientMismatchError(
                f"create_gemini_client() failed: {exc}. "
                f"Deckgen expects:\n{SHARED_CLIENT_EXPECTATIONS}"
            ) from exc

        missing = [
            name
            for name in ("generate_images", "generate_json", "generate_content")
            if not callable(getattr(client, name, None))
        ]
        if missing:
            raise SharedClientMismatchError(
                "GeminiClient is missing required callables: "
                f"{missing}. Deckgen expects:\n{SHARED_CLIENT_EXPECTATIONS}"
            )
        self._shared = client
        logger.info("SharedGeminiClientAdapter._resolve bound ok")
        return client

    async def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        operation: str,
    ) -> GeneratedImage:
        """Delegate image generation to ``GeminiClient.generate_images``.

        Args:
            model: Nano Banana / image model id.
            prompt: Text prompt.
            operation: Operation label for shared logging.

        Returns:
            Normalized ``GeneratedImage`` (inline bytes never logged).

        Raises:
            SharedClientMismatchError: When no image bytes are returned.
        """
        logger.info(
            "SharedGeminiClientAdapter.generate_image model=%s operation=%s "
            "prompt_chars=%s",
            model,
            operation,
            len(prompt),
        )
        client = self._resolve()
        result = await client.generate_images(
            model=model,
            prompt=prompt,
            operation=operation,
            config={"aspect_ratio": IMAGE_ASPECT_RATIO},
        )
        images = getattr(result, "images", ()) or ()
        if not images:
            raise SharedClientMismatchError(
                "generate_images returned no images for "
                f"operation={operation!r}"
            )
        first = images[0]
        data = bytes(getattr(first, "data", b"") or b"")
        mime = getattr(first, "mime_type", None) or IMAGE_MIME_TYPE
        if not data:
            raise SharedClientMismatchError(
                "generate_images returned an empty MediaBlob.data"
            )
        return image_meta(data, mime)

    async def generate_json(
        self,
        *,
        model: str,
        prompt: str,
        operation: str,
        response_schema: dict[str, Any],
        thinking_level: str | None = None,
        image_bytes: bytes | None = None,
        image_mime_type: str | None = None,
    ) -> Any:
        """Delegate JSON GenAI calls; use ``generate_content`` for arrays.

        Args:
            model: Gemini model id.
            prompt: Prompt text.
            operation: Operation label.
            response_schema: JSON schema dict.
            thinking_level: Optional thinking setting.
            image_bytes: Optional image (not logged inline).
            image_mime_type: MIME type for the image part.

        Returns:
            Parsed JSON (dict or list) from the shared client.
        """
        image_len = len(image_bytes) if image_bytes is not None else 0
        logger.info(
            "SharedGeminiClientAdapter.generate_json model=%s operation=%s "
            "thinking_level=%s prompt_chars=%s image_byte_length=%s "
            "schema_keys=%s schema_type=%s",
            model,
            operation,
            thinking_level,
            len(prompt),
            image_len,
            list(response_schema.keys()),
            response_schema.get("type"),
        )
        client = self._resolve()
        contents: Any = prompt
        if image_bytes is not None:
            from app.gemini_client import MediaBlob

            contents = [
                prompt,
                MediaBlob(
                    data=image_bytes,
                    mime_type=image_mime_type or IMAGE_MIME_TYPE,
                ),
            ]

        schema_type = response_schema.get("type")
        level = thinking_level or "low"

        # Shared generate_json enforces dict; arrays go through generate_content.
        if schema_type == "array":
            result = await client.generate_content(
                model=model,
                operation=operation,
                contents=contents,
                response_schema=response_schema,
                thinking_level=level,
            )
            return _coerce_json_value(result)

        return await client.generate_json(
            model=model,
            operation=operation,
            contents=contents,
            response_schema=response_schema,
            thinking_level=level,
        )


def _coerce_json_value(result: Any) -> Any:
    """Extract dict/list JSON from a ``ContentResult`` or raw value.

    Args:
        result: Shared-client ``ContentResult`` or already-parsed JSON.

    Returns:
        Parsed JSON value.

    Raises:
        SharedClientMismatchError: If JSON cannot be extracted.
    """
    if isinstance(result, (dict, list)):
        return result
    parsed = getattr(result, "parsed", None)
    if isinstance(parsed, (dict, list)):
        return parsed
    if hasattr(parsed, "model_dump"):
        dumped = parsed.model_dump()
        if isinstance(dumped, (dict, list)):
            return dumped
    text = (getattr(result, "text", None) or "").strip()
    if text:
        return json.loads(text)
    raise SharedClientMismatchError(
        f"Could not coerce JSON from result type={type(result).__name__}"
    )


def build_client(*, dry_run: bool, fake: FakeDeckGenAIClient | None = None) -> DeckGenAIClient:
    """Construct the GenAI client for dry-run or live mode.

    Args:
        dry_run: When True, return a fake client (no API calls).
        fake: Optional preconfigured fake for tests.

    Returns:
        A ``DeckGenAIClient`` implementation.
    """
    logger.info("build_client called dry_run=%s custom_fake=%s", dry_run, fake is not None)
    if dry_run:
        return fake or FakeDeckGenAIClient()
    return SharedGeminiClientAdapter()
