"""Explicitly gated deterministic triage client for isolated Wave 2 load tests.

This module never imports or calls Gemini. Construction is permitted only when
all three isolation controls are exact: ``APP_ENVIRONMENT=load-test``,
``INSTANCE_MARKER=wave2-load-isolated``, and ``WORKER_FAKE_GEMINI=true``.
Production-like environments fail closed if fake mode is requested, while a
load-test environment fails closed if fake mode is omitted.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from worker.config import WorkerSettings

logger = logging.getLogger(__name__)

_LOAD_ENVIRONMENT = "load-test"
_LOAD_INSTANCE_MARKER = "wave2-load-isolated"


def validate_fake_mode(settings: WorkerSettings) -> bool:
    """Validate load-test isolation and return whether fake mode is enabled.

    Args:
        settings: Worker environment and fake-client configuration.

    Returns:
        ``True`` only for the fully gated isolated fake configuration.

    Raises:
        RuntimeError: If any load/fake control is partial or unsafe.
    """
    logger.info(
        "validate_fake_mode called app_environment=%s instance_marker=%s fake_enabled=%s",
        settings.app_environment,
        settings.instance_marker,
        settings.worker_fake_gemini,
    )
    isolated = (
        settings.app_environment == _LOAD_ENVIRONMENT
        and settings.instance_marker == _LOAD_INSTANCE_MARKER
    )
    if settings.worker_fake_gemini and not isolated:
        raise RuntimeError(
            "WORKER_FAKE_GEMINI requires APP_ENVIRONMENT=load-test and "
            "INSTANCE_MARKER=wave2-load-isolated"
        )
    if isolated and not settings.worker_fake_gemini:
        raise RuntimeError("isolated load-test worker requires WORKER_FAKE_GEMINI=true")
    if settings.app_environment == _LOAD_ENVIRONMENT and not isolated:
        raise RuntimeError("load-test worker requires INSTANCE_MARKER=wave2-load-isolated")
    if settings.instance_marker == _LOAD_INSTANCE_MARKER and not isolated:
        raise RuntimeError("wave2 load instance marker requires APP_ENVIRONMENT=load-test")
    return settings.worker_fake_gemini


class FakeTriageClient:
    """Return deterministic clean triage output with bounded delay/failures."""

    def __init__(
        self,
        *,
        delay_seconds: float,
        failure_rate: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Initialize fake behavior without performing I/O.

        Args:
            delay_seconds: Artificial latency before each result.
            failure_rate: Deterministic fraction of requests that fail.
            sleep: Injectable asynchronous sleeper for zero-wait tests.
        """
        self._delay_seconds = delay_seconds
        self._failure_rate = failure_rate
        self._sleep = sleep
        logger.info(
            "FakeTriageClient initialized delay_seconds=%s failure_rate=%s",
            delay_seconds,
            failure_rate,
        )

    async def triage_audio(
        self,
        *,
        model: str,
        prompt: str,
        response_schema: dict[str, object],
        audio_path: Path,
        thinking_level: str,
    ) -> dict[str, object]:
        """Return clean structured output or a deterministic injected failure.

        Args:
            model: Logged canonical model name; no request is made.
            prompt: Accepted for protocol parity and never persisted.
            response_schema: Accepted for protocol parity.
            audio_path: Used only as deterministic failure-key metadata.
            thinking_level: Accepted for protocol parity.

        Returns:
            Contract-shaped clean triage response.

        Raises:
            RuntimeError: When this request falls in the configured failure set.
        """
        logger.info(
            "FakeTriageClient.triage_audio called model=%s prompt_chars=%s schema_keys=%s "
            "audio_name=%s thinking_level=%s delay_seconds=%s",
            model,
            len(prompt),
            sorted(response_schema),
            audio_path.name,
            thinking_level,
            self._delay_seconds,
        )
        if self._delay_seconds:
            await self._sleep(self._delay_seconds)
        if self._must_fail(audio_path):
            raise RuntimeError("isolated fake triage deterministic failure")
        return {
            "is_speech": True,
            "single_speaker": True,
            "audio_quality_ok": True,
            "is_label_readout": False,
            "readout_reasoning": "Isolated load fixture accepted.",
            "apparent_language_note": "synthetic-load-fixture",
            "duration_estimate_s": 3.0,
            "confidence": 1.0,
        }

    async def aclose(self) -> None:
        """Close the no-op fake client without side effects."""
        logger.info("FakeTriageClient.aclose called")

    def _must_fail(self, audio_path: Path) -> bool:
        """Map an audio filename deterministically into the failure interval.

        Args:
            audio_path: Request path whose filename forms the stable key.

        Returns:
            ``True`` when its bucket is below the configured failure rate.
        """
        logger.info(
            "FakeTriageClient._must_fail called audio_name=%s failure_rate=%s",
            audio_path.name,
            self._failure_rate,
        )
        if self._failure_rate <= 0:
            return False
        if self._failure_rate >= 1:
            return True
        digest = hashlib.sha256(audio_path.name.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:8], "big") / float(2**64)
        return bucket < self._failure_rate


def create_fake_triage_client(
    settings: WorkerSettings,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> FakeTriageClient:
    """Construct the fake client after enforcing all isolation controls.

    Args:
        settings: Fully gated load-test settings.
        sleep: Injectable sleeper for tests.

    Returns:
        Deterministic no-cost triage client.

    Raises:
        RuntimeError: If fake mode is not safely enabled.
    """
    logger.info("create_fake_triage_client called worker_id=%s", settings.worker_id)
    if not validate_fake_mode(settings):
        raise RuntimeError("fake triage client requested while fake mode is disabled")
    return FakeTriageClient(
        delay_seconds=settings.worker_fake_gemini_delay_seconds,
        failure_rate=settings.worker_fake_gemini_failure_rate,
        sleep=sleep,
    )
