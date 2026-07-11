"""Adapt the shared Gemini client to the gauntlet's audio-triage protocol.

The worker owns speech-quality semantics while ``app.gemini_client`` owns SDK
transport, retries, throttling, safe GenAI logs, and API-call instrumentation.
This adapter is the only worker module that translates between those
boundaries: it reads the already-normalized FLAC, wraps bytes as inline media,
and delegates strict JSON generation without duplicating client behavior.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.gemini_client import (
    GeminiClient,
    MediaBlob,
    PostgresApiCallRecorder,
    create_gemini_client,
)
from worker.config import WorkerSettings
from worker.fake_triage import create_fake_triage_client, validate_fake_mode
from worker.models import TriageClient

logger = logging.getLogger(__name__)


class SharedGeminiTriageClient:
    """Expose the worker protocol over the shared Gemini implementation."""

    def __init__(self, client: GeminiClient) -> None:
        """Store the shared client used for triage calls.

        Args:
            client: Configured shared Gemini client.
        """
        logger.info("SharedGeminiTriageClient.__init__ called")
        self._client = client

    async def triage_audio(
        self,
        *,
        model: str,
        prompt: str,
        response_schema: dict[str, object],
        audio_path: Path,
        thinking_level: str,
    ) -> dict[str, object]:
        """Generate strict triage JSON for one normalized FLAC recording.

        Args:
            model: Canonical Gemini model identifier.
            prompt: Worker-owned triage and contamination prompt.
            response_schema: Strict JSON schema expected by the worker.
            audio_path: Path to normalized FLAC input.
            thinking_level: Gemini thinking level for the classification.

        Returns:
            Parsed JSON object conforming to ``response_schema``.

        Side effects:
            Reads the FLAC and performs one retry-managed Gemini operation.
            Logs only path and byte-count metadata, never inline audio.
        """
        audio_bytes = await asyncio.to_thread(audio_path.read_bytes)
        logger.info(
            "SharedGeminiTriageClient.triage_audio called model=%s prompt=%s "
            "thinking_level=%s schema_keys=%s audio_path=%s audio_bytes=%s",
            model,
            prompt,
            thinking_level,
            sorted(response_schema),
            audio_path,
            len(audio_bytes),
        )
        result = await self._client.generate_json(
            model=model,
            operation="gauntlet_triage",
            contents=[prompt, MediaBlob(audio_bytes, "audio/flac")],
            response_schema=response_schema,
            thinking_level=thinking_level,
        )
        return {str(key): value for key, value in result.items()}

    async def aclose(self) -> None:
        """Close transport resources owned by the shared Gemini client."""
        logger.info("SharedGeminiTriageClient.aclose called")
        await self._client.aclose()


def create_triage_client(pool: Any | None = None) -> SharedGeminiTriageClient:
    """Create the production gauntlet adapter with optional DB instrumentation.

    Args:
        pool: Asyncpg-compatible pool used for best-effort ``api_calls`` rows.

    Returns:
        A worker-compatible adapter around the configured shared Gemini client.
    """
    logger.info("create_triage_client called recorder_enabled=%s", pool is not None)
    recorder = PostgresApiCallRecorder(pool) if pool is not None else None
    client = create_gemini_client(get_settings(), recorder=recorder)
    return SharedGeminiTriageClient(client)


def create_configured_triage_client(
    settings: WorkerSettings,
    pool: Any | None = None,
) -> TriageClient:
    """Select paid production or no-cost isolated-load triage explicitly.

    Args:
        settings: Worker settings containing the three fake-mode gates.
        pool: Asyncpg-compatible pool for production API-call instrumentation.

    Returns:
        A fake client only in the fully isolated load environment, otherwise the
        shared production Gemini adapter.

    Raises:
        RuntimeError: If load/fake controls are partial or unsafe.
    """
    logger.info(
        "create_configured_triage_client called worker_id=%s app_environment=%s "
        "fake_enabled=%s",
        settings.worker_id,
        settings.app_environment,
        settings.worker_fake_gemini,
    )
    if validate_fake_mode(settings):
        return create_fake_triage_client(settings)
    return create_triage_client(pool)
