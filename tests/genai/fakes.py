"""Fake GenAI transport and recorder for ``tests/genai`` smoke coverage.

These fakes implement the ``GeminiTransport`` and ``ApiCallRecorder``
protocols from ``app.gemini_client`` so unit tests exercise retry, JSON
parsing, sanitization, and instrumentation without network or database I/O.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from google.genai import errors as genai_errors
from google.genai import types

from app.gemini_client import MediaBlob

logger = logging.getLogger(__name__)


@dataclass
class FakeContentResponse:
    """Minimal generateContent response shape used by the client.

    Attributes:
        text: Model text output.
        parsed: Optional pre-parsed object (dict or pydantic-like).
    """

    text: str = ""
    parsed: Any | None = None


@dataclass
class FakeImage:
    """Minimal image wrapper matching SDK attribute names."""

    image_bytes: bytes
    mime_type: str = "image/png"


@dataclass
class FakeGeneratedImage:
    """Wrapper with an ``image`` attribute like the SDK response."""

    image: FakeImage


@dataclass
class FakeImagesResponse:
    """Minimal generateImages response shape."""

    generated_images: list[FakeGeneratedImage] = field(default_factory=list)


class FakeGeminiTransport:
    """Scripted async transport for deterministic client tests.

    Attributes:
        content_queue: FIFO of responses or exceptions for generate_content.
        image_queue: FIFO of responses or exceptions for generate_images.
        content_calls: Recorded generate_content kwargs.
        image_calls: Recorded generate_images kwargs.
        closed: Whether ``aclose`` was awaited.
    """

    def __init__(
        self,
        *,
        content_results: Sequence[Any] | None = None,
        image_results: Sequence[Any] | None = None,
    ) -> None:
        """Initialize scripted result queues.

        Args:
            content_results: Responses or exceptions yielded in order.
            image_results: Image responses or exceptions yielded in order.
        """
        logger.info(
            "FakeGeminiTransport.__init__ called content_n=%s image_n=%s",
            len(content_results or []),
            len(image_results or []),
        )
        self.content_queue: list[Any] = list(content_results or [])
        self.image_queue: list[Any] = list(image_results or [])
        self.content_calls: list[dict[str, Any]] = []
        self.image_calls: list[dict[str, Any]] = []
        self.closed = False

    async def generate_content(
        self,
        *,
        model: str,
        contents: Any,
        config: types.GenerateContentConfig | None,
    ) -> Any:
        """Return the next scripted content result or raise it.

        Args:
            model: Model id passed by the client.
            contents: Normalized contents payload.
            config: Optional generate-content config.

        Returns:
            The next ``FakeContentResponse`` (or compatible object).

        Raises:
            Exception: When the next queued item is an exception instance.
            AssertionError: When the queue is empty.
        """
        logger.info(
            "FakeGeminiTransport.generate_content called model=%s "
            "queue_remaining=%s",
            model,
            len(self.content_queue),
        )
        self.content_calls.append(
            {"model": model, "contents": contents, "config": config}
        )
        if not self.content_queue:
            raise AssertionError("FakeGeminiTransport content_queue exhausted")
        item = self.content_queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item) and not isinstance(item, type):
            produced = item()
            if isinstance(produced, BaseException):
                raise produced
            return produced
        return item

    async def generate_images(
        self,
        *,
        model: str,
        prompt: str,
        config: types.GenerateImagesConfig | None,
    ) -> Any:
        """Return the next scripted image result or raise it.

        Args:
            model: Model id passed by the client.
            prompt: Image prompt.
            config: Optional image config.

        Returns:
            The next ``FakeImagesResponse`` (or compatible object).

        Raises:
            Exception: When the next queued item is an exception instance.
            AssertionError: When the queue is empty.
        """
        logger.info(
            "FakeGeminiTransport.generate_images called model=%s "
            "prompt_chars=%s queue_remaining=%s",
            model,
            len(prompt),
            len(self.image_queue),
        )
        self.image_calls.append({"model": model, "prompt": prompt, "config": config})
        if not self.image_queue:
            raise AssertionError("FakeGeminiTransport image_queue exhausted")
        item = self.image_queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def aclose(self) -> None:
        """Mark the fake transport closed."""
        logger.info("FakeGeminiTransport.aclose called")
        self.closed = True


class FakeApiCallRecorder:
    """In-memory ``ApiCallRecorder`` that never touches Postgres."""

    def __init__(
        self,
        *,
        fail_on_record: bool = False,
        on_record: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Create a recorder that stores rows in ``self.rows``.

        Args:
            fail_on_record: When True, raises to exercise swallow behavior.
            on_record: Optional callback invoked with each row dict.
        """
        logger.info(
            "FakeApiCallRecorder.__init__ called fail_on_record=%s",
            fail_on_record,
        )
        self.rows: list[dict[str, Any]] = []
        self.fail_on_record = fail_on_record
        self.on_record = on_record

    async def record(
        self,
        *,
        model: str,
        operation: str,
        request_meta: Any,
        response_meta: Any,
        status: str,
        latency_ms: int | None,
        estimated_cost_microusd: int | None,
    ) -> None:
        """Append one instrumentation row or raise when configured to fail.

        Args:
            model: Model identifier.
            operation: Operation label.
            request_meta: Sanitized request metadata.
            response_meta: Sanitized response metadata.
            status: ``success`` or ``error``.
            latency_ms: Optional latency.
            estimated_cost_microusd: Optional cost estimate.

        Returns:
            None.

        Raises:
            RuntimeError: When ``fail_on_record`` is True.
        """
        logger.info(
            "FakeApiCallRecorder.record called model=%s operation=%s status=%s",
            model,
            operation,
            status,
        )
        row = {
            "model": model,
            "operation": operation,
            "request_meta": dict(request_meta),
            "response_meta": dict(response_meta),
            "status": status,
            "latency_ms": latency_ms,
            "estimated_cost_microusd": estimated_cost_microusd,
        }
        if self.on_record is not None:
            self.on_record(row)
        if self.fail_on_record:
            raise RuntimeError("fake recorder forced failure")
        self.rows.append(row)


def api_error(code: int, status: str = "RESOURCE_EXHAUSTED") -> genai_errors.APIError:
    """Build a ``genai_errors.APIError`` for retry tests.

    Args:
        code: HTTP-like status code.
        status: Status string embedded in the error payload.

    Returns:
        An ``APIError`` instance with the given code/status.
    """
    logger.info("api_error called code=%s status=%s", code, status)
    return genai_errors.APIError(code, {"error": {"code": code, "status": status}})


def sample_audio_blob(n: int = 32) -> MediaBlob:
    """Create a tiny FLAC-like media blob for multimodal tests.

    Args:
        n: Number of placeholder bytes.

    Returns:
        A ``MediaBlob`` with ``audio/flac`` MIME type.
    """
    logger.info("sample_audio_blob called n=%s", n)
    return MediaBlob(data=b"\x00" * n, mime_type="audio/flac")
