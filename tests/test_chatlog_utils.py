"""Tests for pure helper functions in sources/exports/chatlog.py."""

from __future__ import annotations

import pytest

from lynchpin.sources.exports.chatlog import (
    _coerce_int,
    _infer_timestamp,
    _slug_from_metadata,
)


# ---------------------------------------------------------------------------
# _coerce_int
# ---------------------------------------------------------------------------

class TestCoerceInt:
    def test_none_returns_default(self) -> None:
        assert _coerce_int(None) is None
        assert _coerce_int(None, default=0) == 0

    def test_integer_passthrough(self) -> None:
        assert _coerce_int(42) == 42

    def test_string_integer_parsed(self) -> None:
        assert _coerce_int("17") == 17

    def test_float_truncated(self) -> None:
        assert _coerce_int(3.9) == 3

    def test_invalid_returns_default(self) -> None:
        assert _coerce_int("not a number") is None
        assert _coerce_int("not a number", default=-1) == -1

    def test_negative_integer(self) -> None:
        assert _coerce_int(-5) == -5


# ---------------------------------------------------------------------------
# _slug_from_metadata
# ---------------------------------------------------------------------------

class TestSlugFromMetadata:
    def test_polylogue_slug_preferred(self) -> None:
        metadata = {"polylogue": {"slug": "sinex-session"}, "slug": "other-slug"}
        assert _slug_from_metadata(metadata) == "sinex-session"

    def test_polylogue_title_fallback(self) -> None:
        metadata = {"polylogue": {"title": "Great Session"}}
        assert _slug_from_metadata(metadata) == "Great Session"

    def test_top_level_slug_used(self) -> None:
        metadata = {"slug": "top-level-slug"}
        assert _slug_from_metadata(metadata) == "top-level-slug"

    def test_top_level_title_used(self) -> None:
        metadata = {"title": "My Chat"}
        assert _slug_from_metadata(metadata) == "My Chat"

    def test_empty_metadata_returns_none(self) -> None:
        assert _slug_from_metadata({}) is None

    def test_whitespace_slug_returns_none(self) -> None:
        assert _slug_from_metadata({"slug": "   "}) is None

    def test_non_dict_polylogue_skipped(self) -> None:
        # polylogue is not a dict, falls through to top-level
        metadata = {"polylogue": "not-a-dict", "slug": "top-slug"}
        assert _slug_from_metadata(metadata) == "top-slug"


# ---------------------------------------------------------------------------
# _infer_timestamp
# ---------------------------------------------------------------------------

class TestInferTimestamp:
    def test_timestamp_from_session_path(self) -> None:
        # Pattern: YYYY-MM-DDT HH-MM-SS
        metadata = {"sessionPath": "sessions/2026-03-17T10-30-00.jsonl"}
        result = _infer_timestamp(metadata, None)
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 17

    def test_timestamp_from_slug(self) -> None:
        slug = "coding-2026-03-17T14-22-05-sinex"
        result = _infer_timestamp({}, slug)
        assert result is not None
        assert result.hour == 14

    def test_no_timestamp_returns_none(self) -> None:
        assert _infer_timestamp({"title": "no dates here"}, None) is None

    def test_empty_metadata_and_slug_returns_none(self) -> None:
        assert _infer_timestamp({}, None) is None

    def test_source_id_tried(self) -> None:
        metadata = {"sourceId": "chatlog-2026-03-17T08-15-00"}
        result = _infer_timestamp(metadata, None)
        assert result is not None
        assert result.year == 2026
