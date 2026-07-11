"""Tests for encoded-image format detection at the deck publication boundary.

Gemini image models may return PNG, JPEG, or WebP regardless of the requested
card format. These tests ensure published filenames match the actual payload so
FastAPI serves the correct media type without calling Gemini or touching disk.
"""

from __future__ import annotations

import logging

import pytest

from deckgen.publish import image_file_extension

logger = logging.getLogger(__name__)


@pytest.mark.parametrize(
    ("payload", "extension"),
    [
        (b"\x89PNG\r\n\x1a\npayload", ".png"),
        (b"\xff\xd8\xff\xe0payload", ".jpg"),
        (b"RIFF\x04\x00\x00\x00WEBPpayload", ".webp"),
    ],
)
def test_image_file_extension_detects_supported_encodings(
    payload: bytes,
    extension: str,
) -> None:
    """Map each supported image signature to its browser-safe extension.

    Args:
        payload: Representative encoded image prefix and payload.
        extension: Expected filename extension.
    """
    logger.info(
        "test_image_file_extension_detects_supported_encodings called "
        "byte_length=%s expected=%s",
        len(payload),
        extension,
    )
    assert image_file_extension(payload) == extension


def test_image_file_extension_rejects_unknown_payload() -> None:
    """Reject unknown bytes instead of publishing misleading media metadata."""
    logger.info("test_image_file_extension_rejects_unknown_payload called")
    with pytest.raises(ValueError, match="unsupported generated image encoding"):
        image_file_extension(b"not-an-image")
