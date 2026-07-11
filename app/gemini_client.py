"""Async thin Gemini / Nano Banana client for gauntlet and deck agents.

Provides one shared wrapper around ``google-genai`` so worker and deckgen do
not each invent retry, rate-limit, logging, or model-string policy. Callers
import model IDs from ``app.models`` only, obtain the API key from
``app.config.Settings``, and optionally inject a transport (for fakes) and an
``ApiCallRecorder`` (for best-effort ``api_calls`` rows) without coupling this
module to FastAPI.

Major use cases:
- Gauntlet: ``generate_json`` with audio inline parts + strict response schema.
- Deckgen: ``generate_images`` (NB2 Lite) and ``generate_json`` / ``generate_content``
  for verification, decoys, and translations.
- Tests / dry-run: inject ``GeminiTransport`` fakes; zero network or DB I/O.

Architectural boundary:
- Lives under ``app/`` as shared infrastructure; does not import FastAPI.
- Does not own prompts (those stay in ``worker/prompts.py`` / ``deckgen/prompts.py``).
- Postgres instrumentation is optional and best-effort: recorder failures are
  logged and never fail the GenAI call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.config import Settings, get_settings, gemini_settings_log_meta
from app.models import GEMINI_FLASH, GEMINI_MODELS, NANO_BANANA_LITE

logger = logging.getLogger(__name__)

# HTTP / SDK status codes treated as transient (retryable).
_TRANSIENT_STATUS_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})

# Keys whose values must never appear in logs.
_SECRET_KEY_FRAGMENTS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "credential",
    }
)

# Keys that typically hold inline media / base64 payloads.
_MEDIA_KEY_FRAGMENTS: frozenset[str] = frozenset(
    {
        "image_bytes",
        "inline_data",
        "data",
        "audio",
        "base64",
        "blob",
        "bytes",
        "file_data",
    }
)


@dataclass(frozen=True)
class MediaBlob:
    """Binary media payload with MIME type for multimodal Gemini inputs.

    Attributes:
        data: Raw bytes (audio FLAC, image PNG/JPEG, etc.).
        mime_type: IANA media type, e.g. ``audio/flac`` or ``image/png``.
    """

    data: bytes
    mime_type: str


@dataclass(frozen=True)
class ContentResult:
    """Normalized text / JSON result from ``generate_content``.

    Attributes:
        text: Model text output (JSON string when structured output is used).
        parsed: SDK- or client-parsed object when available; otherwise ``None``.
        model: Canonical model id from ``app.models``.
        operation: Caller-supplied operation label for logs and ``api_calls``.
        latency_ms: Wall time for the successful attempt only.
        attempts: Total attempts including retries.
    """

    text: str
    parsed: Any | None
    model: str
    operation: str
    latency_ms: int
    attempts: int


@dataclass(frozen=True)
class ImageResult:
    """Normalized image-generation result from ``generate_images``.

    Attributes:
        images: Generated image blobs (empty if the model returned none).
        model: Canonical model id from ``app.models``.
        operation: Caller-supplied operation label for logs and ``api_calls``.
        latency_ms: Wall time for the successful attempt only.
        attempts: Total attempts including retries.
    """

    images: tuple[MediaBlob, ...]
    model: str
    operation: str
    latency_ms: int
    attempts: int


@runtime_checkable
class GeminiTransport(Protocol):
    """Minimal async transport used by ``GeminiClient`` (real SDK or fake)."""

    async def generate_content(
        self,
        *,
        model: str,
        contents: Any,
        config: types.GenerateContentConfig | None,
    ) -> Any:
        """Execute one generateContent call.

        Args:
            model: Canonical Gemini model id.
            contents: SDK-compatible contents payload.
            config: Optional generate-content configuration.

        Returns:
            An object exposing ``.text`` and optionally ``.parsed``.
        """

    async def generate_images(
        self,
        *,
        model: str,
        prompt: str,
        config: types.GenerateImagesConfig | None,
    ) -> Any:
        """Execute one generateImages call.

        Args:
            model: Canonical Nano Banana / image model id.
            prompt: Text prompt for image generation.
            config: Optional image-generation configuration.

        Returns:
            An object with ``generated_images`` (list of SDK image wrappers).
        """

    async def aclose(self) -> None:
        """Release transport resources (no-op for fakes)."""


@runtime_checkable
class ApiCallRecorder(Protocol):
    """Best-effort sink for ``api_calls`` instrumentation rows."""

    async def record(
        self,
        *,
        model: str,
        operation: str,
        request_meta: Mapping[str, Any],
        response_meta: Mapping[str, Any],
        status: str,
        latency_ms: int | None,
        estimated_cost_microusd: int | None,
    ) -> None:
        """Persist one API call row.

        Args:
            model: Model identifier.
            operation: Caller operation label.
            request_meta: Sanitized request metadata JSON object.
            response_meta: Sanitized response metadata JSON object.
            status: ``success`` or ``error`` per ``contracts/schema.sql``.
            latency_ms: Optional latency in milliseconds.
            estimated_cost_microusd: Optional micro-USD cost estimate.

        Returns:
            None. Implementations must not raise into the GenAI hot path;
            ``GeminiClient`` already wraps recorder failures.
        """


class GoogleGenaiTransport:
    """Production transport wrapping ``google.genai.Client`` async models API.

    Attributes:
        _client: Underlying GenAI client (owns HTTP resources).
    """

    def __init__(self, api_key: str) -> None:
        """Create a Google GenAI async transport.

        Args:
            api_key: Gemini API key from settings (not logged here).

        Side effects:
            Constructs an SDK client. Does not perform network I/O until a
            generate method is awaited.
        """
        logger.info(
            "GoogleGenaiTransport.__init__ called key_configured=%s key_length=%s",
            bool(api_key),
            len(api_key),
        )
        self._client = genai.Client(api_key=api_key)

    async def generate_content(
        self,
        *,
        model: str,
        contents: Any,
        config: types.GenerateContentConfig | None,
    ) -> Any:
        """Delegate to ``client.aio.models.generate_content``.

        Args:
            model: Canonical Gemini model id.
            contents: SDK-compatible contents payload.
            config: Optional generate-content configuration.

        Returns:
            ``GenerateContentResponse`` from the SDK.
        """
        logger.info(
            "GoogleGenaiTransport.generate_content called model=%s "
            "has_config=%s contents_meta=%s",
            model,
            config is not None,
            summarize_contents(contents),
        )
        return await self._client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

    async def generate_images(
        self,
        *,
        model: str,
        prompt: str,
        config: types.GenerateImagesConfig | None,
    ) -> Any:
        """Generate a native Gemini image through ``generate_content``.

        Args:
            model: Canonical image model id.
            prompt: Text prompt for image generation.
            config: Optional image-generation options. Native Gemini image
                models support one image per call; compatible aspect ratio,
                image size, and person-generation values are translated to
                ``ImageConfig``.

        Returns:
            ``GenerateContentResponse`` containing image ``inline_data`` parts.

        Raises:
            ValueError: When Imagen-only options are supplied to a native
                Gemini image model.
        """
        logger.info(
            "GoogleGenaiTransport.generate_images called model=%s "
            "prompt_chars=%s has_config=%s",
            model,
            len(prompt),
            config is not None,
        )
        image_config: types.ImageConfig | None = None
        if config is not None:
            values = config.model_dump(exclude_none=True)
            number_of_images = values.pop("number_of_images", 1)
            if number_of_images != 1:
                raise ValueError("native Gemini image models support one image per call")
            supported = {
                key: values.pop(key)
                for key in ("aspect_ratio", "image_size", "person_generation")
                if key in values
            }
            if values:
                raise ValueError(
                    "unsupported native Gemini image options: "
                    f"{sorted(values)}"
                )
            if supported:
                image_config = types.ImageConfig(**supported)
        generation_config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=image_config,
        )
        return await self._client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=generation_config,
        )

    async def aclose(self) -> None:
        """Close the underlying async GenAI client.

        Side effects:
            Releases SDK HTTP resources via ``aclose``.
        """
        logger.info("GoogleGenaiTransport.aclose called")
        await self._client.aio.aclose()


class PostgresApiCallRecorder:
    """Best-effort ``api_calls`` writer using an asyncpg-like pool.

    Accepts any object with ``execute(sql, *args)`` (asyncpg ``Pool`` or
    ``Connection``). Does not import FastAPI or ``app.database``.
    """

    def __init__(self, pool: Any) -> None:
        """Bind a pool/connection used for instrumentation inserts.

        Args:
            pool: Async object exposing ``await pool.execute(...)``.
        """
        logger.info(
            "PostgresApiCallRecorder.__init__ called pool_type=%s",
            type(pool).__name__,
        )
        self._pool = pool

    async def record(
        self,
        *,
        model: str,
        operation: str,
        request_meta: Mapping[str, Any],
        response_meta: Mapping[str, Any],
        status: str,
        latency_ms: int | None,
        estimated_cost_microusd: int | None,
    ) -> None:
        """Insert one row into ``api_calls``.

        Args:
            model: Model identifier.
            operation: Caller operation label.
            request_meta: Sanitized request metadata.
            response_meta: Sanitized response metadata.
            status: ``success`` or ``error``.
            latency_ms: Optional latency in milliseconds.
            estimated_cost_microusd: Optional micro-USD estimate.

        Returns:
            None.

        Side effects:
            Executes one INSERT. Callers should treat failures as non-fatal;
            ``GeminiClient`` already swallows recorder exceptions.
        """
        logger.info(
            "PostgresApiCallRecorder.record called model=%s operation=%s "
            "status=%s latency_ms=%s cost_microusd=%s",
            model,
            operation,
            status,
            latency_ms,
            estimated_cost_microusd,
        )
        await self._pool.execute(
            """
            INSERT INTO api_calls (
                model, operation, request_meta, response_meta,
                status, latency_ms, estimated_cost_microusd
            )
            VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6, $7)
            """,
            model,
            operation,
            json.dumps(dict(request_meta)),
            json.dumps(dict(response_meta)),
            status,
            latency_ms,
            estimated_cost_microusd,
        )


class _ModelGate:
    """Per-model concurrency semaphore plus sliding-window RPM limiter."""

    def __init__(self, *, max_concurrency: int, rpm: int) -> None:
        """Configure concurrency and requests-per-minute limits.

        Args:
            max_concurrency: Maximum in-flight calls for this model.
            rpm: Soft maximum starts per rolling 60-second window.
        """
        logger.info(
            "_ModelGate.__init__ called max_concurrency=%s rpm=%s",
            max_concurrency,
            rpm,
        )
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._rpm = max(1, rpm)
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until concurrency and RPM budgets allow another call.

        Side effects:
            May sleep to respect the rolling RPM window, then acquires the
            concurrency semaphore.
        """
        logger.info(
            "_ModelGate.acquire called rpm=%s in_flight_wait=True",
            self._rpm,
        )
        await self._semaphore.acquire()
        try:
            async with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= 60.0:
                    self._timestamps.popleft()
                if len(self._timestamps) >= self._rpm:
                    sleep_for = 60.0 - (now - self._timestamps[0]) + 0.001
                    logger.info(
                        "_ModelGate.acquire rpm_wait sleep_s=%.3f",
                        sleep_for,
                    )
                    await asyncio.sleep(max(sleep_for, 0.0))
                    now = time.monotonic()
                    while self._timestamps and now - self._timestamps[0] >= 60.0:
                        self._timestamps.popleft()
                self._timestamps.append(time.monotonic())
        except Exception:
            self._semaphore.release()
            raise

    def release(self) -> None:
        """Release the concurrency semaphore after a call completes."""
        logger.info("_ModelGate.release called")
        self._semaphore.release()


def inline_part(blob: MediaBlob) -> types.Part:
    """Build a Gemini ``Part`` from an inline media blob.

    Args:
        blob: Raw bytes and MIME type (audio or image).

    Returns:
        A ``types.Part`` with ``inline_data`` set. Bytes are not logged.
    """
    logger.info(
        "inline_part called mime_type=%s byte_length=%s",
        blob.mime_type,
        len(blob.data),
    )
    return types.Part(
        inline_data=types.Blob(data=blob.data, mime_type=blob.mime_type),
    )


def summarize_contents(contents: Any) -> dict[str, Any]:
    """Produce safe metadata describing a contents payload for logs.

    Args:
        contents: String, part, list, or nested SDK content structure.

    Returns:
        A JSON-serializable summary with types and byte lengths only — never
        inline media bytes, base64, or secrets.
    """
    logger.info(
        "summarize_contents called contents_type=%s",
        type(contents).__name__,
    )
    return {"contents": sanitize_for_log(contents)}


def sanitize_for_log(value: Any, *, _key: str | None = None) -> Any:
    """Recursively redact secrets and inline media for INFO logging.

    Args:
        value: Arbitrary request/response fragment.
        _key: Parent mapping key used to detect media/secret fields.

    Returns:
        A JSON-friendly structure safe to log. Binary payloads become
        ``{"type": "...", "byte_length": N}``; secret fields become
        ``{"redacted": True, "length": N}``.
    """
    key_l = (_key or "").lower()
    if _key is not None and any(frag in key_l for frag in _SECRET_KEY_FRAGMENTS):
        if isinstance(value, str):
            return {"redacted": True, "length": len(value)}
        return {"redacted": True, "type": type(value).__name__}

    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "bytes", "byte_length": len(value)}

    if isinstance(value, MediaBlob):
        return {
            "type": "MediaBlob",
            "mime_type": value.mime_type,
            "byte_length": len(value.data),
        }

    if isinstance(value, types.Part):
        return sanitize_for_log(value.model_dump(exclude_none=True), _key=_key)

    if isinstance(value, types.Blob):
        return {
            "type": "Blob",
            "mime_type": value.mime_type,
            "byte_length": len(value.data) if value.data is not None else 0,
            "display_name": value.display_name,
        }

    if isinstance(value, types.Image):
        return {
            "type": "Image",
            "mime_type": value.mime_type,
            "byte_length": len(value.image_bytes) if value.image_bytes else 0,
            "gcs_uri_present": bool(value.gcs_uri),
        }

    if _key is not None and any(frag in key_l for frag in _MEDIA_KEY_FRAGMENTS):
        if isinstance(value, str):
            return {
                "type": "media_string",
                "char_length": len(value),
                "looks_base64": _looks_like_base64(value),
            }
        if isinstance(value, Mapping):
            return {
                "type": type(value).__name__,
                "keys": sorted(str(k) for k in value.keys()),
            }
        return {"type": type(value).__name__, "redacted_media": True}

    if isinstance(value, Mapping):
        return {str(k): sanitize_for_log(v, _key=str(k)) for k, v in value.items()}

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_for_log(item) for item in value]

    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            dumped = value.model_dump(exclude_none=True)
        except Exception:
            return {"type": type(value).__name__}
        return sanitize_for_log(dumped, _key=_key)

    if isinstance(value, str) and len(value) > 4000:
        return {"type": "str", "char_length": len(value), "preview": value[:256]}

    return value


def _looks_like_base64(text: str) -> bool:
    """Heuristic: long strings without spaces may be base64 media.

    Args:
        text: Candidate string.

    Returns:
        True when the string is long and base64-alphabet-like.
    """
    if len(text) < 256 or " " in text[:64]:
        return False
    sample = text[:80].replace("\n", "")
    alphabet = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
    return set(sample) <= alphabet


def is_transient_error(exc: BaseException) -> bool:
    """Return whether an exception should be retried with backoff.

    Args:
        exc: Exception raised by the transport or network stack.

    Returns:
        True for rate limits, timeouts, connection errors, and 5xx/429-class
        GenAI API errors.
    """
    logger.info(
        "is_transient_error called exc_type=%s",
        type(exc).__name__,
    )
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None)
        if isinstance(code, int) and code in _TRANSIENT_STATUS_CODES:
            return True
        status = str(getattr(exc, "status", "") or "").upper()
        if status in {"RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED", "ABORTED"}:
            return True
    return False


def normalize_contents(
    contents: str | MediaBlob | types.Part | Sequence[Any],
) -> Any:
    """Normalize caller-friendly contents into SDK-compatible values.

    Args:
        contents: A prompt string, media blob, Part, or sequence of those.

    Returns:
        A value acceptable to ``generate_content`` (string or list of parts).
    """
    logger.info(
        "normalize_contents called contents_type=%s",
        type(contents).__name__,
    )
    if isinstance(contents, str):
        return contents
    if isinstance(contents, MediaBlob):
        return [inline_part(contents)]
    if isinstance(contents, types.Part):
        return [contents]
    if isinstance(contents, Sequence):
        parts: list[Any] = []
        for item in contents:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, MediaBlob):
                parts.append(inline_part(item))
            else:
                parts.append(item)
        return parts
    return contents


class GeminiClient:
    """Async Gemini wrapper with rate limits, retries, and safe logging.

    Construct via ``create_gemini_client`` or directly with an injected
    ``GeminiTransport`` for tests. Model ids must come from ``app.models``.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        transport: GeminiTransport,
        recorder: ApiCallRecorder | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        """Bind settings, transport, and optional instrumentation.

        Args:
            settings: Application settings (retry and per-model limits).
            transport: Real ``GoogleGenaiTransport`` or a test fake.
            recorder: Optional ``api_calls`` sink; failures are swallowed.
            sleep: Awaitable sleep used for backoff (injectable in tests).

        Side effects:
            Builds per-model gates from settings. Does not perform network I/O.
        """
        logger.info(
            "GeminiClient.__init__ called transport=%s recorder=%s settings=%s",
            type(transport).__name__,
            type(recorder).__name__ if recorder else None,
            gemini_settings_log_meta(settings),
        )
        self._settings = settings
        self._transport = transport
        self._recorder = recorder
        self._sleep = sleep or asyncio.sleep
        self._gates: dict[str, _ModelGate] = {
            GEMINI_FLASH: _ModelGate(
                max_concurrency=settings.gemini_flash_max_concurrency,
                rpm=settings.gemini_flash_rpm,
            ),
            NANO_BANANA_LITE: _ModelGate(
                max_concurrency=settings.nano_banana_max_concurrency,
                rpm=settings.nano_banana_rpm,
            ),
        }

    async def aclose(self) -> None:
        """Close the underlying transport.

        Side effects:
            Awaits ``transport.aclose()``.
        """
        logger.info("GeminiClient.aclose called")
        await self._transport.aclose()

    async def generate_content(
        self,
        *,
        model: str,
        operation: str,
        contents: str | MediaBlob | types.Part | Sequence[Any],
        config: types.GenerateContentConfig | Mapping[str, Any] | None = None,
        response_schema: Any | None = None,
        response_mime_type: str | None = None,
        thinking_level: str | None = None,
    ) -> ContentResult:
        """Call Gemini generateContent with retries and instrumentation.

        Args:
            model: Must be a member of ``app.models.GEMINI_MODELS``.
            operation: Stable label for logs / ``api_calls`` (e.g. ``triage``).
            contents: Prompt text and/or ``MediaBlob`` / ``Part`` sequence.
            config: Full ``GenerateContentConfig`` or dict merged with helpers.
            response_schema: When set, enables structured JSON output.
            response_mime_type: Overrides MIME type (default ``application/json``
                when ``response_schema`` is set).
            thinking_level: Optional thinking level (e.g. ``low`` for triage).

        Returns:
            ``ContentResult`` with text, optional parsed object, and timing.

        Raises:
            ValueError: Unknown model id.
            Exception: Last transport error after retries are exhausted.
        """
        self._require_model(model)
        resolved = self._resolve_content_config(
            config=config,
            response_schema=response_schema,
            response_mime_type=response_mime_type,
            thinking_level=thinking_level,
        )
        normalized = normalize_contents(contents)
        request_meta = {
            "operation": operation,
            "prompt": summarize_contents(normalized),
            "config": sanitize_for_log(
                resolved.model_dump(exclude_none=True) if resolved else None
            ),
        }
        logger.info(
            "GeminiClient.generate_content request model=%s operation=%s "
            "request_meta=%s",
            model,
            operation,
            request_meta,
        )

        async def _call() -> ContentResult:
            response = await self._transport.generate_content(
                model=model,
                contents=normalized,
                config=resolved,
            )
            text = getattr(response, "text", None) or ""
            parsed = getattr(response, "parsed", None)
            return ContentResult(
                text=text,
                parsed=parsed,
                model=model,
                operation=operation,
                latency_ms=0,
                attempts=0,
            )

        result, latency_ms, attempts = await self._run_with_policy(
            model=model,
            operation=operation,
            request_meta=request_meta,
            call=_call,
            cost_fn=lambda _r: None,
        )
        # Rebuild with accurate timing/attempts (inner result used latency 0).
        final = ContentResult(
            text=result.text,
            parsed=result.parsed,
            model=model,
            operation=operation,
            latency_ms=latency_ms,
            attempts=attempts,
        )
        logger.info(
            "GeminiClient.generate_content response model=%s operation=%s "
            "latency_ms=%s attempts=%s output=%s",
            model,
            operation,
            latency_ms,
            attempts,
            sanitize_for_log({"text": final.text, "parsed": final.parsed}),
        )
        return final

    async def generate_json(
        self,
        *,
        model: str,
        operation: str,
        contents: str | MediaBlob | types.Part | Sequence[Any],
        response_schema: Any,
        thinking_level: str = "low",
        config: types.GenerateContentConfig | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate strict JSON and return a dictionary.

        Args:
            model: Canonical Gemini model id (typically ``GEMINI_FLASH``).
            operation: Caller operation label.
            contents: Prompt and optional media parts.
            response_schema: JSON schema dict or SDK-compatible schema object.
            thinking_level: Thinking level; default ``low`` for triage latency.
            config: Optional extra generate-content configuration.

        Returns:
            Parsed JSON object as a ``dict``.

        Raises:
            ValueError: When the model output is not a JSON object.
            Exception: Propagates transport failures after retries.
        """
        logger.info(
            "GeminiClient.generate_json called model=%s operation=%s "
            "thinking_level=%s schema_type=%s",
            model,
            operation,
            thinking_level,
            type(response_schema).__name__,
        )
        result = await self.generate_content(
            model=model,
            operation=operation,
            contents=contents,
            config=config,
            response_schema=response_schema,
            response_mime_type="application/json",
            thinking_level=thinking_level,
        )
        parsed = result.parsed
        if isinstance(parsed, dict):
            return parsed
        if hasattr(parsed, "model_dump"):
            dumped = parsed.model_dump()
            if isinstance(dumped, dict):
                return dumped
        text = result.text.strip()
        data = json.loads(text) if text else None
        if not isinstance(data, dict):
            raise ValueError(
                f"generate_json expected a JSON object for operation={operation!r}"
            )
        return data

    async def generate_images(
        self,
        *,
        model: str,
        operation: str,
        prompt: str,
        config: types.GenerateImagesConfig | Mapping[str, Any] | None = None,
    ) -> ImageResult:
        """Generate images via Nano Banana (or another allowed image model).

        Args:
            model: Must be in ``GEMINI_MODELS`` (typically ``NANO_BANANA_LITE``).
            operation: Caller operation label (e.g. ``deck_image``).
            prompt: Text-to-image prompt (logged as text; no media).
            config: ``GenerateImagesConfig`` or dict of image options.

        Returns:
            ``ImageResult`` with ``MediaBlob`` images (bytes stripped from logs).

        Raises:
            ValueError: Unknown model id.
            Exception: Last transport error after retries are exhausted.
        """
        self._require_model(model)
        resolved = self._resolve_image_config(config)
        request_meta = {
            "operation": operation,
            "prompt": prompt,
            "config": sanitize_for_log(
                resolved.model_dump(exclude_none=True) if resolved else None
            ),
        }
        logger.info(
            "GeminiClient.generate_images request model=%s operation=%s "
            "request_meta=%s",
            model,
            operation,
            request_meta,
        )

        async def _call() -> ImageResult:
            response = await self._transport.generate_images(
                model=model,
                prompt=prompt,
                config=resolved,
            )
            images: list[MediaBlob] = []
            for candidate in getattr(response, "candidates", None) or []:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", None) or []:
                    inline_data = getattr(part, "inline_data", None)
                    raw = getattr(inline_data, "data", None) or b""
                    mime = getattr(inline_data, "mime_type", None) or "image/png"
                    if raw and mime.startswith("image/"):
                        images.append(MediaBlob(data=raw, mime_type=mime))
            # Retain compatibility with injected Imagen-shaped test transports.
            for generated in getattr(response, "generated_images", None) or []:
                image = getattr(generated, "image", None)
                if image is None:
                    continue
                raw = getattr(image, "image_bytes", None) or b""
                mime = getattr(image, "mime_type", None) or "image/png"
                images.append(MediaBlob(data=raw, mime_type=mime))
            return ImageResult(
                images=tuple(images),
                model=model,
                operation=operation,
                latency_ms=0,
                attempts=0,
            )

        def _cost(result: ImageResult) -> int | None:
            if model != NANO_BANANA_LITE:
                return None
            per = self._settings.nano_banana_cost_microusd_per_image
            return per * len(result.images)

        result, latency_ms, attempts = await self._run_with_policy(
            model=model,
            operation=operation,
            request_meta=request_meta,
            call=_call,
            cost_fn=_cost,
        )
        final = ImageResult(
            images=result.images,
            model=model,
            operation=operation,
            latency_ms=latency_ms,
            attempts=attempts,
        )
        logger.info(
            "GeminiClient.generate_images response model=%s operation=%s "
            "latency_ms=%s attempts=%s output=%s",
            model,
            operation,
            latency_ms,
            attempts,
            sanitize_for_log(
                {
                    "image_count": len(final.images),
                    "images": [
                        {"mime_type": img.mime_type, "byte_length": len(img.data)}
                        for img in final.images
                    ],
                }
            ),
        )
        return final

    def _require_model(self, model: str) -> None:
        """Reject model strings that are not pinned in ``app.models``.

        Args:
            model: Candidate model id.

        Raises:
            ValueError: When ``model`` is not in ``GEMINI_MODELS``.
        """
        logger.info("_require_model called model=%s", model)
        if model not in GEMINI_MODELS:
            raise ValueError(
                f"model {model!r} is not a canonical Gemini id; "
                f"import from app.models ({sorted(GEMINI_MODELS)})"
            )

    def _resolve_content_config(
        self,
        *,
        config: types.GenerateContentConfig | Mapping[str, Any] | None,
        response_schema: Any | None,
        response_mime_type: str | None,
        thinking_level: str | None,
    ) -> types.GenerateContentConfig | None:
        """Merge helper kwargs into a ``GenerateContentConfig``.

        Args:
            config: Existing config object or mapping.
            response_schema: Optional structured-output schema.
            response_mime_type: Optional response MIME override.
            thinking_level: Optional thinking level string.

        Returns:
            A ``GenerateContentConfig`` or ``None`` when nothing was provided.
        """
        logger.info(
            "_resolve_content_config called has_config=%s has_schema=%s "
            "mime=%s thinking_level=%s",
            config is not None,
            response_schema is not None,
            response_mime_type,
            thinking_level,
        )
        data: MutableMapping[str, Any]
        if config is None:
            data = {}
        elif isinstance(config, types.GenerateContentConfig):
            data = dict(config.model_dump(exclude_none=True))
        else:
            data = dict(config)

        if response_schema is not None:
            data["response_schema"] = response_schema
            data.setdefault("response_mime_type", "application/json")
        if response_mime_type is not None:
            data["response_mime_type"] = response_mime_type
        if thinking_level is not None:
            existing = data.get("thinking_config") or {}
            if isinstance(existing, types.ThinkingConfig):
                existing = existing.model_dump(exclude_none=True)
            thinking = dict(existing)
            thinking["thinking_level"] = thinking_level
            data["thinking_config"] = thinking

        if not data:
            return None
        return types.GenerateContentConfig(**data)

    def _resolve_image_config(
        self,
        config: types.GenerateImagesConfig | Mapping[str, Any] | None,
    ) -> types.GenerateImagesConfig | None:
        """Normalize image config mapping into ``GenerateImagesConfig``.

        Args:
            config: Config object, mapping, or ``None``.

        Returns:
            A ``GenerateImagesConfig`` or ``None``.
        """
        logger.info(
            "_resolve_image_config called has_config=%s",
            config is not None,
        )
        if config is None:
            return None
        if isinstance(config, types.GenerateImagesConfig):
            return config
        return types.GenerateImagesConfig(**dict(config))

    async def _run_with_policy(
        self,
        *,
        model: str,
        operation: str,
        request_meta: Mapping[str, Any],
        call: Callable[[], Awaitable[Any]],
        cost_fn: Callable[[Any], int | None],
    ) -> tuple[Any, int, int]:
        """Apply rate limits, retries, logging, and best-effort recording.

        Args:
            model: Canonical model id.
            operation: Operation label.
            request_meta: Already-sanitized request metadata.
            call: Zero-arg async callable performing one transport attempt.
            cost_fn: Maps a successful result to micro-USD or ``None``.

        Returns:
            Tuple of ``(result, latency_ms, attempts)``.

        Raises:
            Exception: Re-raises the last failure after retries are exhausted.
        """
        max_attempts = self._settings.gemini_max_retries + 1
        gate = self._gates.get(model) or self._gates[GEMINI_FLASH]
        last_exc: BaseException | None = None

        for attempt in range(1, max_attempts + 1):
            await gate.acquire()
            started = time.perf_counter()
            try:
                result = await call()
                latency_ms = int((time.perf_counter() - started) * 1000)
                response_meta = {
                    "attempt": attempt,
                    "result": sanitize_for_log(_result_log_payload(result)),
                }
                await self._safe_record(
                    model=model,
                    operation=operation,
                    request_meta=request_meta,
                    response_meta=response_meta,
                    status="success",
                    latency_ms=latency_ms,
                    estimated_cost_microusd=cost_fn(result),
                )
                return result, latency_ms, attempt
            except Exception as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                last_exc = exc
                transient = is_transient_error(exc)
                logger.info(
                    "GeminiClient attempt failed model=%s operation=%s "
                    "attempt=%s/%s transient=%s exc_type=%s",
                    model,
                    operation,
                    attempt,
                    max_attempts,
                    transient,
                    type(exc).__name__,
                )
                await self._safe_record(
                    model=model,
                    operation=operation,
                    request_meta=request_meta,
                    response_meta={
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "transient": transient,
                    },
                    status="error",
                    latency_ms=latency_ms,
                    estimated_cost_microusd=None,
                )
                if not transient or attempt >= max_attempts:
                    break
                delay = min(
                    self._settings.gemini_retry_base_delay_s * (2 ** (attempt - 1)),
                    self._settings.gemini_retry_max_delay_s,
                )
                delay *= 0.5 + random.random()
                logger.info(
                    "GeminiClient backoff model=%s operation=%s sleep_s=%.3f",
                    model,
                    operation,
                    delay,
                )
                await self._sleep(delay)
            finally:
                gate.release()

        assert last_exc is not None
        raise last_exc

    async def _safe_record(
        self,
        *,
        model: str,
        operation: str,
        request_meta: Mapping[str, Any],
        response_meta: Mapping[str, Any],
        status: str,
        latency_ms: int | None,
        estimated_cost_microusd: int | None,
    ) -> None:
        """Invoke the optional recorder without failing the GenAI call.

        Args:
            model: Model identifier.
            operation: Operation label.
            request_meta: Sanitized request metadata.
            response_meta: Sanitized response metadata.
            status: ``success`` or ``error``.
            latency_ms: Optional latency.
            estimated_cost_microusd: Optional cost estimate.

        Side effects:
            Awaits ``recorder.record`` when configured; logs and swallows
            recorder exceptions.
        """
        if self._recorder is None:
            return
        try:
            await self._recorder.record(
                model=model,
                operation=operation,
                request_meta=request_meta,
                response_meta=response_meta,
                status=status,
                latency_ms=latency_ms,
                estimated_cost_microusd=estimated_cost_microusd,
            )
        except Exception as exc:
            logger.info(
                "GeminiClient recorder failure swallowed model=%s operation=%s "
                "exc_type=%s",
                model,
                operation,
                type(exc).__name__,
            )


def _result_log_payload(result: Any) -> Any:
    """Build a log-safe summary of a successful client result object.

    Args:
        result: ``ContentResult``, ``ImageResult``, or other value.

    Returns:
        A mapping suitable for ``sanitize_for_log``.
    """
    if isinstance(result, ContentResult):
        return {"text": result.text, "parsed": result.parsed}
    if isinstance(result, ImageResult):
        return {
            "image_count": len(result.images),
            "images": [
                {"mime_type": img.mime_type, "byte_length": len(img.data)}
                for img in result.images
            ],
        }
    return {"type": type(result).__name__}


def create_gemini_client(
    settings: Settings | None = None,
    *,
    transport: GeminiTransport | None = None,
    recorder: ApiCallRecorder | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> GeminiClient:
    """Factory for a configured ``GeminiClient``.

    Args:
        settings: Optional settings; defaults to ``get_settings()``.
        transport: Optional transport override (required for keyless tests).
        recorder: Optional ``api_calls`` instrumentation sink.
        sleep: Optional backoff sleep (tests inject a no-op / fake clock).

    Returns:
        A ready ``GeminiClient``. When ``transport`` is omitted, builds
        ``GoogleGenaiTransport`` from ``settings.gemini_api_key``.

    Raises:
        ValueError: When no transport is provided and the API key is empty.

    Side effects:
        May construct an SDK client. Logs safe settings metadata only.
    """
    resolved = settings or get_settings()
    logger.info(
        "create_gemini_client called transport_injected=%s recorder=%s "
        "settings=%s",
        transport is not None,
        type(recorder).__name__ if recorder else None,
        gemini_settings_log_meta(resolved),
    )
    if transport is None:
        if not resolved.gemini_api_key:
            raise ValueError(
                "gemini_api_key is empty; set GEMINI_API_KEY or inject a transport"
            )
        transport = GoogleGenaiTransport(resolved.gemini_api_key)
    return GeminiClient(
        settings=resolved,
        transport=transport,
        recorder=recorder,
        sleep=sleep,
    )
