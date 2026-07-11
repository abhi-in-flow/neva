"""Verify the gauntlet-to-shared-client adapter without network or database I/O.

The test writes synthetic bytes to pytest's temporary directory, injects a
minimal fake shared client, and confirms the adapter sends inline FLAC metadata
and strict configuration while returning parsed JSON unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.gemini_client import MediaBlob
from worker.gemini_adapter import SharedGeminiTriageClient


class FakeSharedClient:
    """Capture adapter calls while returning a deterministic triage response."""

    def __init__(self) -> None:
        """Initialize empty call state."""
        self.call: dict[str, Any] | None = None
        self.closed = False

    async def generate_json(self, **kwargs: Any) -> dict[str, object]:
        """Capture keyword arguments and return fake structured output."""
        self.call = kwargs
        return {"is_speech": True}

    async def aclose(self) -> None:
        """Record that the adapter closed its transport."""
        self.closed = True


@pytest.mark.asyncio
async def test_adapter_wraps_flac_and_delegates(tmp_path: Path) -> None:
    """Pass FLAC bytes to the shared client without making a GenAI call.

    Args:
        tmp_path: Isolated directory for the synthetic FLAC file.
    """
    audio_path = tmp_path / "sample.flac"
    audio_path.write_bytes(b"synthetic-flac")
    fake = FakeSharedClient()
    adapter = SharedGeminiTriageClient(fake)  # type: ignore[arg-type]

    result = await adapter.triage_audio(
        model="gemini-3.5-flash",
        prompt="classify",
        response_schema={"type": "object"},
        audio_path=audio_path,
        thinking_level="low",
    )

    assert result == {"is_speech": True}
    assert fake.call is not None
    assert fake.call["operation"] == "gauntlet_triage"
    assert fake.call["thinking_level"] == "low"
    media = fake.call["contents"][1]
    assert isinstance(media, MediaBlob)
    assert media.data == b"synthetic-flac"
    assert media.mime_type == "audio/flac"

    await adapter.aclose()
    assert fake.closed is True
