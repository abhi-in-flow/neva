"""Tests for asyncpg JSONB normalization at the worker repository boundary."""

from __future__ import annotations

import pytest

from worker.repository import _json_list, _json_object


def test_json_object_accepts_asyncpg_text_and_mapping() -> None:
    """JSONB text and fake-driver mappings normalize identically."""
    expected = {"en": "fish", "verified": True}
    assert _json_object('{"en":"fish","verified":true}', field="label") == expected
    assert _json_object(expected, field="label") == expected


def test_json_list_accepts_asyncpg_text_and_list() -> None:
    """JSONB arrays normalize from encoded text and fake-driver lists."""
    expected = ["en", "hi"]
    assert _json_list('["en","hi"]', field="languages") == expected
    assert _json_list(expected, field="languages") == expected


def test_json_normalizers_reject_wrong_shapes() -> None:
    """Object/list shape mismatches fail instead of corrupting domain values."""
    with pytest.raises(ValueError, match="JSON object"):
        _json_object('["not-an-object"]', field="label")
    with pytest.raises(ValueError, match="JSON list"):
        _json_list('{"not":"a-list"}', field="languages")
