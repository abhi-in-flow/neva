"""Focused smoke tests for ``app.gemini_client`` using fakes only.

Covers structured JSON, image generation, retries/backoff, model pinning,
safe logging redaction, and best-effort ``api_calls`` recording. These tests
perform zero live Gemini calls and zero database or runtime-data mutations.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.config import Settings
from app.gemini_client import (
    GeminiClient,
    MediaBlob,
    create_gemini_client,
    inline_part,
    is_transient_error,
    sanitize_for_log,
)
from app.models import GEMINI_FLASH, NANO_BANANA_LITE
from tests.genai.fakes import (
    FakeApiCallRecorder,
    FakeContentResponse,
    FakeGeneratedImage,
    FakeGeminiTransport,
    FakeImage,
    FakeImagesResponse,
    api_error,
    sample_audio_blob,
)

logger = logging.getLogger(__name__)


def _settings(**overrides: Any) -> Settings:
    """Build isolated settings with fast retries for unit tests.

    Args:
        **overrides: Field overrides applied on top of safe defaults.

    Returns:
        A ``Settings`` instance that does not read production secrets for
        these tests (API key left empty; transport is always injected).
    """
    logger.info("_settings called overrides=%s", sorted(overrides))
    base = {
        "gemini_api_key": "",
        "gemini_max_retries": 2,
        "gemini_retry_base_delay_s": 0.01,
        "gemini_retry_max_delay_s": 0.05,
        "gemini_flash_max_concurrency": 2,
        "gemini_flash_rpm": 120,
        "nano_banana_max_concurrency": 2,
        "nano_banana_rpm": 120,
        "nano_banana_cost_microusd_per_image": 33600,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def sleeps() -> list[float]:
    """Collect backoff sleep durations injected into the client.

    Returns:
        A mutable list that the fake sleep appends to.
    """
    logger.info("sleeps fixture setup")
    return []


@pytest.fixture
def fake_sleep(sleeps: list[float]):
    """Return an async sleep that records delays without waiting.

    Args:
        sleeps: Shared list collecting requested sleep durations.

    Returns:
        An async callable matching ``asyncio.sleep``'s awaitable shape.
    """
    logger.info("fake_sleep fixture setup")

    async def _sleep(delay: float) -> None:
        logger.info("fake_sleep called delay=%s", delay)
        sleeps.append(delay)

    return _sleep


async def test_generate_json_returns_parsed_dict(fake_sleep) -> None:
    """Assert structured JSON path returns a dict from ``parsed``.

    Args:
        fake_sleep: Injected no-op backoff.

    Returns:
        None.
    """
    logger.info("test_generate_json_returns_parsed_dict called")
    transport = FakeGeminiTransport(
        content_results=[
            FakeContentResponse(
                text='{"is_speech": true, "confidence": 0.9}',
                parsed={"is_speech": True, "confidence": 0.9},
            )
        ]
    )
    recorder = FakeApiCallRecorder()
    client = GeminiClient(
        settings=_settings(),
        transport=transport,
        recorder=recorder,
        sleep=fake_sleep,
    )
    audio = sample_audio_blob()
    result = await client.generate_json(
        model=GEMINI_FLASH,
        operation="triage",
        contents=["Describe quality.", audio],
        response_schema={
            "type": "object",
            "properties": {
                "is_speech": {"type": "boolean"},
                "confidence": {"type": "number"},
            },
            "required": ["is_speech", "confidence"],
        },
        thinking_level="low",
    )
    assert result == {"is_speech": True, "confidence": 0.9}
    assert len(transport.content_calls) == 1
    call = transport.content_calls[0]
    assert call["model"] == GEMINI_FLASH
    assert call["config"] is not None
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].thinking_config is not None
    level = call["config"].thinking_config.thinking_level
    assert str(getattr(level, "value", level)).lower() == "low"
    assert recorder.rows and recorder.rows[0]["status"] == "success"
    assert recorder.rows[0]["operation"] == "triage"
    logger.info("test_generate_json_returns_parsed_dict completed")


async def test_generate_json_parses_text_when_parsed_missing(fake_sleep) -> None:
    """Assert JSON text fallback when SDK ``parsed`` is absent.

    Args:
        fake_sleep: Injected no-op backoff.

    Returns:
        None.
    """
    logger.info("test_generate_json_parses_text_when_parsed_missing called")
    transport = FakeGeminiTransport(
        content_results=[
            FakeContentResponse(text='{"ok": true, "label": "tea"}', parsed=None)
        ]
    )
    client = create_gemini_client(
        _settings(),
        transport=transport,
        sleep=fake_sleep,
    )
    data = await client.generate_json(
        model=GEMINI_FLASH,
        operation="verify",
        contents="Does the image match?",
        response_schema={"type": "object"},
    )
    assert data == {"ok": True, "label": "tea"}
    logger.info("test_generate_json_parses_text_when_parsed_missing completed")


async def test_generate_images_returns_blobs_and_cost(fake_sleep) -> None:
    """Assert image generation returns MediaBlobs and estimates cost.

    Args:
        fake_sleep: Injected no-op backoff.

    Returns:
        None.
    """
    logger.info("test_generate_images_returns_blobs_and_cost called")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    transport = FakeGeminiTransport(
        image_results=[
            FakeImagesResponse(
                generated_images=[
                    FakeGeneratedImage(image=FakeImage(image_bytes=png))
                ]
            )
        ]
    )
    recorder = FakeApiCallRecorder()
    client = GeminiClient(
        settings=_settings(),
        transport=transport,
        recorder=recorder,
        sleep=fake_sleep,
    )
    result = await client.generate_images(
        model=NANO_BANANA_LITE,
        operation="deck_image",
        prompt="A clay tea cup on a wooden table, no text",
    )
    assert len(result.images) == 1
    assert result.images[0].data == png
    assert result.images[0].mime_type == "image/png"
    assert recorder.rows[0]["estimated_cost_microusd"] == 33600
    # Response meta must not embed raw image bytes.
    dumped = str(recorder.rows[0]["response_meta"])
    assert png not in dumped.encode("utf-8", errors="ignore")
    assert "byte_length" in dumped or "image_count" in dumped
    logger.info("test_generate_images_returns_blobs_and_cost completed")


async def test_retries_transient_then_succeeds(fake_sleep, sleeps: list[float]) -> None:
    """Assert 429-class errors retry with backoff then succeed.

    Args:
        fake_sleep: Injected no-op backoff.
        sleeps: Collected backoff delays.

    Returns:
        None.
    """
    logger.info("test_retries_transient_then_succeeds called")
    transport = FakeGeminiTransport(
        content_results=[
            api_error(429),
            FakeContentResponse(text='{"ok": true}', parsed={"ok": True}),
        ]
    )
    recorder = FakeApiCallRecorder()
    client = GeminiClient(
        settings=_settings(gemini_max_retries=3),
        transport=transport,
        recorder=recorder,
        sleep=fake_sleep,
    )
    data = await client.generate_json(
        model=GEMINI_FLASH,
        operation="triage",
        contents="ping",
        response_schema={"type": "object"},
    )
    assert data == {"ok": True}
    assert len(transport.content_calls) == 2
    assert sleeps  # backoff invoked
    statuses = [row["status"] for row in recorder.rows]
    assert statuses == ["error", "success"]
    logger.info("test_retries_transient_then_succeeds completed")


async def test_non_transient_error_does_not_retry(fake_sleep, sleeps: list[float]) -> None:
    """Assert 400-class errors fail immediately without backoff.

    Args:
        fake_sleep: Injected no-op backoff.
        sleeps: Collected backoff delays.

    Returns:
        None.
    """
    logger.info("test_non_transient_error_does_not_retry called")
    transport = FakeGeminiTransport(content_results=[api_error(400, "INVALID_ARGUMENT")])
    client = GeminiClient(
        settings=_settings(gemini_max_retries=3),
        transport=transport,
        sleep=fake_sleep,
    )
    with pytest.raises(Exception):
        await client.generate_content(
            model=GEMINI_FLASH,
            operation="bad",
            contents="x",
        )
    assert len(transport.content_calls) == 1
    assert sleeps == []
    logger.info("test_non_transient_error_does_not_retry completed")


async def test_unknown_model_rejected(fake_sleep) -> None:
    """Assert non-canonical model strings raise ``ValueError``.

    Args:
        fake_sleep: Injected no-op backoff.

    Returns:
        None.
    """
    logger.info("test_unknown_model_rejected called")
    client = GeminiClient(
        settings=_settings(),
        transport=FakeGeminiTransport(),
        sleep=fake_sleep,
    )
    with pytest.raises(ValueError, match="canonical Gemini"):
        await client.generate_content(
            model="gemini-pro-invented",
            operation="x",
            contents="hi",
        )
    logger.info("test_unknown_model_rejected completed")


async def test_create_client_requires_key_without_transport() -> None:
    """Assert factory refuses empty API key when transport is omitted.

    Returns:
        None.
    """
    logger.info("test_create_client_requires_key_without_transport called")
    with pytest.raises(ValueError, match="gemini_api_key"):
        create_gemini_client(_settings(gemini_api_key=""))
    logger.info("test_create_client_requires_key_without_transport completed")


async def test_recorder_failure_is_swallowed(fake_sleep) -> None:
    """Assert instrumentation failures never fail a successful GenAI call.

    Args:
        fake_sleep: Injected no-op backoff.

    Returns:
        None.
    """
    logger.info("test_recorder_failure_is_swallowed called")
    transport = FakeGeminiTransport(
        content_results=[FakeContentResponse(text="hello", parsed=None)]
    )
    client = GeminiClient(
        settings=_settings(),
        transport=transport,
        recorder=FakeApiCallRecorder(fail_on_record=True),
        sleep=fake_sleep,
    )
    result = await client.generate_content(
        model=GEMINI_FLASH,
        operation="noop",
        contents="hello",
    )
    assert result.text == "hello"
    logger.info("test_recorder_failure_is_swallowed completed")


async def test_sanitize_strips_inline_media_and_secrets() -> None:
    """Assert sanitize_for_log redacts bytes, base64-ish data, and API keys.

    Returns:
        None.
    """
    logger.info("test_sanitize_strips_inline_media_and_secrets called")
    blob = MediaBlob(data=b"\xff" * 64, mime_type="audio/flac")
    part = inline_part(blob)
    payload = {
        "api_key": "secret-value-do-not-log",
        "inline_data": part.inline_data,
        "prompt": "safe text",
        "image_bytes": b"\x01\x02\x03",
        "nested": {"data": "A" * 300},
    }
    cleaned = sanitize_for_log(payload)
    assert cleaned["prompt"] == "safe text"
    assert cleaned["api_key"]["redacted"] is True
    assert "secret-value" not in str(cleaned)
    assert cleaned["image_bytes"]["type"] == "bytes"
    assert cleaned["image_bytes"]["byte_length"] == 3
    assert "byte_length" in cleaned["inline_data"] or "mime_type" in cleaned["inline_data"]
    assert "A" * 50 not in str(cleaned["nested"])
    logger.info("test_sanitize_strips_inline_media_and_secrets completed")


def test_is_transient_error_classification() -> None:
    """Assert transient vs non-transient classification helpers.

    Returns:
        None.
    """
    logger.info("test_is_transient_error_classification called")
    assert is_transient_error(api_error(429))
    assert is_transient_error(api_error(503, "UNAVAILABLE"))
    assert is_transient_error(TimeoutError())
    assert not is_transient_error(api_error(400, "INVALID_ARGUMENT"))
    assert not is_transient_error(ValueError("nope"))
    logger.info("test_is_transient_error_classification completed")


async def test_aclose_closes_transport(fake_sleep) -> None:
    """Assert client aclose propagates to the transport.

    Args:
        fake_sleep: Injected no-op backoff.

    Returns:
        None.
    """
    logger.info("test_aclose_closes_transport called")
    transport = FakeGeminiTransport()
    client = GeminiClient(settings=_settings(), transport=transport, sleep=fake_sleep)
    await client.aclose()
    assert transport.closed is True
    logger.info("test_aclose_closes_transport completed")
