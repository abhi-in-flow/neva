"""Redact GenAI request and response metadata for browser-facing admin traces.

The gauntlet stores triage prompts that may include card labels. Admin UI must
never replay those strings. This module converts stored JSON into length-only
prompt markers, truncated safe strings, and opaque nested maps.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from app.admin_ops.config import ADMIN_META_STRING_MAX_CHARS, ADMIN_PROMPT_PREVIEW_CHARS

logger = logging.getLogger(__name__)

_PROMPT_KEYS = frozenset({"prompt", "contents", "text", "input_text", "message"})
_SECRET_FRAGMENTS = ("key", "token", "secret", "password", "authorization", "credential")
_MEDIA_FRAGMENTS = ("audio", "image", "bytes", "base64", "data", "blob", "flac", "webm")


def redact_admin_meta(value: Any, *, _key: str | None = None) -> Any:
    """Return a JSON-safe, privacy-preserving copy of stored call metadata.

    Args:
        value: Arbitrary ``request_meta`` or ``response_meta`` fragment.
        _key: Parent mapping key used to detect prompt, secret, and media fields.

    Returns:
        A structure safe for the admin traces panel. Prompt fields become
        ``{"redacted": True, "char_length": N}``. Secrets and media are length
        or type markers only.
    """
    if _key is None:
        logger.info(
            "redact_admin_meta called value_type=%s",
            type(value).__name__,
        )
    key_l = (_key or "").lower()

    if _key is not None and any(frag in key_l for frag in _SECRET_FRAGMENTS):
        if isinstance(value, str):
            return {"redacted": True, "length": len(value)}
        return {"redacted": True, "type": type(value).__name__}

    if _key is not None and key_l in _PROMPT_KEYS:
        if isinstance(value, str):
            payload: dict[str, object] = {
                "redacted": True,
                "char_length": len(value),
            }
            if ADMIN_PROMPT_PREVIEW_CHARS > 0:
                payload["preview"] = value[:ADMIN_PROMPT_PREVIEW_CHARS]
            return payload
        if isinstance(value, Mapping):
            return {
                "redacted": True,
                "type": type(value).__name__,
                "keys": sorted(str(k) for k in value.keys()),
            }
        return {"redacted": True, "type": type(value).__name__}

    if _key is not None and any(frag in key_l for frag in _MEDIA_FRAGMENTS):
        if isinstance(value, str):
            return {"type": "media_string", "char_length": len(value)}
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {"type": "bytes", "byte_length": len(value)}
        return {"type": type(value).__name__, "redacted_media": True}

    if isinstance(value, Mapping):
        return {str(k): redact_admin_meta(v, _key=str(k)) for k, v in value.items()}

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_admin_meta(item) for item in value]

    if isinstance(value, str) and len(value) > ADMIN_META_STRING_MAX_CHARS:
        return {
            "type": "str",
            "char_length": len(value),
            "preview": value[:ADMIN_META_STRING_MAX_CHARS],
        }

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    return {"type": type(value).__name__}
