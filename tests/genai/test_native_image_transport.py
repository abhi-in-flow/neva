"""Verify Nano Banana uses Gemini native image generation, not Imagen.

Google's current Gemini API documentation routes
``gemini-3.1-flash-lite-image`` through ``generate_content`` with IMAGE response
modality. These tests fake the SDK object graph to prove the production
transport selects that endpoint and the shared client extracts returned inline
image parts without making a paid API call.
"""

from __future__ import annotations

from typing import Any

import pytest
from google.genai import types

from app.config import Settings
from app.gemini_client import GeminiClient, GoogleGenaiTransport
from app.models import NANO_BANANA_LITE
from tests.genai.fakes import FakeGeminiTransport


class FakeModels:
    """Capture calls made to the SDK models surface."""

    def __init__(self, response: Any) -> None:
        """Store the response and initialize call capture."""
        self.response = response
        self.content_call: dict[str, Any] | None = None
        self.generate_images_called = False

    async def generate_content(self, **kwargs: Any) -> Any:
        """Capture native image generation and return the scripted response."""
        self.content_call = kwargs
        return self.response

    async def generate_images(self, **_kwargs: Any) -> Any:
        """Fail if the Imagen-only endpoint is selected."""
        self.generate_images_called = True
        raise AssertionError("Nano Banana must not use models.generate_images")


class FakeAio:
    """Expose a fake models API and close hook."""

    def __init__(self, models: FakeModels) -> None:
        """Bind fake model methods."""
        self.models = models

    async def aclose(self) -> None:
        """Provide the transport close interface."""


class FakeSdkClient:
    """Provide the ``aio`` attribute expected by the production transport."""

    def __init__(self, models: FakeModels) -> None:
        """Create a fake async SDK surface."""
        self.aio = FakeAio(models)


@pytest.mark.asyncio
async def test_transport_uses_generate_content_image_modality() -> None:
    """Assert the production transport selects Gemini's native image endpoint."""
    models = FakeModels(response=object())
    transport = GoogleGenaiTransport("placeholder")
    transport._client = FakeSdkClient(models)  # type: ignore[assignment]

    await transport.generate_images(
        model=NANO_BANANA_LITE,
        prompt="one clay cup",
        config=types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio="1:1",
            image_size="1K",
        ),
    )

    assert models.generate_images_called is False
    assert models.content_call is not None
    assert models.content_call["model"] == NANO_BANANA_LITE
    assert models.content_call["contents"] == "one clay cup"
    config = models.content_call["config"]
    assert config.response_modalities == ["IMAGE"]
    assert config.image_config.aspect_ratio == "1:1"
    assert config.image_config.image_size == "1K"


@pytest.mark.asyncio
async def test_client_extracts_native_inline_image() -> None:
    """Normalize an inline image part from a native Gemini response."""
    async def no_sleep(_delay: float) -> None:
        """Replace policy delays during this deterministic test."""

    png = b"\x89PNG\r\n\x1a\nnative"
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    parts=[types.Part.from_bytes(data=png, mime_type="image/png")]
                )
            )
        ]
    )
    transport = FakeGeminiTransport(image_results=[response])
    client = GeminiClient(
        settings=Settings(
            gemini_api_key="",
            nano_banana_max_concurrency=1,
            nano_banana_rpm=60,
        ),
        transport=transport,
        sleep=no_sleep,
    )

    result = await client.generate_images(
        model=NANO_BANANA_LITE,
        operation="deck_image",
        prompt="one clay cup",
    )

    assert len(result.images) == 1
    assert result.images[0].data == png
    assert result.images[0].mime_type == "image/png"
