"""Shared fixtures for deckgen unit tests.

All fixtures stay in-memory: fake GenAI clients and publishers so tests never
call Gemini, touch Postgres, or write under the runtime ``data/`` tree.
"""

from __future__ import annotations

import logging

import pytest

from deckgen.client import FakeDeckGenAIClient
from deckgen.publish import InMemoryPublisher

logger = logging.getLogger(__name__)


def _pass_verdict() -> dict:
    """Build a verification JSON payload that the pipeline accepts.

    Returns:
        A pass verdict dict matching ``VERIFY_RESPONSE_SCHEMA``.
    """
    return {
        "depicts_label": True,
        "has_text": False,
        "has_ambiguity": False,
        "competing_interpretation": None,
        "cultural_ok": True,
        "verdict": "pass",
        "reason": "clear subject",
    }


def _fail_verdict(*, cultural_ok: bool = True, reason: str = "ambiguous") -> dict:
    """Build a verification JSON payload that the pipeline rejects.

    Args:
        cultural_ok: Cultural gate flag.
        reason: One-line reject reason.

    Returns:
        A fail verdict dict.
    """
    return {
        "depicts_label": False,
        "has_text": False,
        "has_ambiguity": True,
        "competing_interpretation": "something else",
        "cultural_ok": cultural_ok,
        "verdict": "fail",
        "reason": reason,
    }


@pytest.fixture
def pass_verdict() -> dict:
    """Fixture exposing a passing verification payload."""
    logger.info("pass_verdict fixture")
    return _pass_verdict()


@pytest.fixture
def fail_verdict() -> dict:
    """Fixture exposing a failing verification payload."""
    logger.info("fail_verdict fixture")
    return _fail_verdict()


@pytest.fixture
def fake_client() -> FakeDeckGenAIClient:
    """Default fake client that always passes verification.

    Returns:
        ``FakeDeckGenAIClient`` with empty script (defaults to pass).
    """
    logger.info("fake_client fixture")
    return FakeDeckGenAIClient()


@pytest.fixture
def memory_publisher() -> InMemoryPublisher:
    """In-memory publisher that records live decks without I/O.

    Returns:
        A successful ``InMemoryPublisher``.
    """
    logger.info("memory_publisher fixture")
    return InMemoryPublisher()
