"""Session token helpers for bearer authentication.

Issues opaque session tokens at join time and stores only SHA-256 hashes in
Postgres (``players.session_token_hash``). Callers must never log raw tokens;
use truncated fingerprints for diagnostics.
"""

from __future__ import annotations

import hashlib
import logging
import secrets

logger = logging.getLogger(__name__)


def issue_session_token() -> str:
    """Create a new opaque session bearer token.

    Returns:
        A URL-safe random token string suitable for ``Authorization: Bearer``.

    Side effects:
        Reads from the OS CSPRNG. Logs only that a token was issued.
    """
    logger.info("issue_session_token called")
    token = secrets.token_urlsafe(32)
    logger.info("issue_session_token completed token_len=%s", len(token))
    return token


def hash_session_token(token: str) -> str:
    """Hash a session token for durable storage and lookup.

    Args:
        token: Raw bearer token from join or the Authorization header.

    Returns:
        Lowercase hex SHA-256 digest of the UTF-8 token bytes.

    Side effects:
        Logs token length only; never logs the token or digest in full.
    """
    logger.info("hash_session_token called token_len=%s", len(token))
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    logger.info("hash_session_token completed digest_prefix=%s", digest[:8])
    return digest


def token_fingerprint(token: str) -> str:
    """Return a short non-reversible fingerprint for safe logs.

    Args:
        token: Raw bearer token.

    Returns:
        The first eight hex characters of the token hash.
    """
    return hash_session_token(token)[:8]
