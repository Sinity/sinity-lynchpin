"""Tests for pure helper functions in sources/exports/wykop.py."""

from __future__ import annotations

from datetime import datetime

import pytest

from lynchpin.sources.exports.wykop import (
    _as_list,
    _as_str,
    _month_in_range,
    _month_key,
    _parse_datetime,
    _safe_int,
)


# ---------------------------------------------------------------------------
# _month_key
# ---------------------------------------------------------------------------

class TestMonthKey:
    def test_basic_format(self) -> None:
        dt = datetime(2026, 3, 17)
        assert _month_key(dt) == "2026-03"

    def test_zero_padded_month(self) -> None:
        assert _month_key(datetime(2026, 1, 1)) == "2026-01"

    def test_december(self) -> None:
        assert _month_key(datetime(2026, 12, 31)) == "2026-12"


# ---------------------------------------------------------------------------
# _month_in_range
# ---------------------------------------------------------------------------

class TestMonthInRange:
    def test_within_range(self) -> None:
        assert _month_in_range("2026-03", "2026-01", "2026-06") is True

    def test_at_start(self) -> None:
        assert _month_in_range("2026-01", "2026-01", "2026-06") is True

    def test_at_end(self) -> None:
        assert _month_in_range("2026-06", "2026-01", "2026-06") is True

    def test_before_range(self) -> None:
        assert _month_in_range("2025-12", "2026-01", "2026-06") is False

    def test_after_range(self) -> None:
        assert _month_in_range("2026-07", "2026-01", "2026-06") is False


# ---------------------------------------------------------------------------
# _parse_datetime
# ---------------------------------------------------------------------------

class TestParseDatetime:
    def test_valid_datetime_format(self) -> None:
        # DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
        result = _parse_datetime("2026-03-17 10:30:00")
        assert result is not None
        assert result.year == 2026
        assert result.hour == 10

    def test_invalid_format_returns_none(self) -> None:
        assert _parse_datetime("not-a-date") is None

    def test_none_returns_none(self) -> None:
        assert _parse_datetime(None) is None

    def test_non_string_returns_none(self) -> None:
        assert _parse_datetime(42) is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_datetime("") is None


# ---------------------------------------------------------------------------
# _safe_int
# ---------------------------------------------------------------------------

class TestSafeInt:
    def test_integer_passthrough(self) -> None:
        assert _safe_int(42) == 42

    def test_string_integer_parsed(self) -> None:
        assert _safe_int("17") == 17

    def test_whitespace_stripped(self) -> None:
        assert _safe_int("  5  ") == 5

    def test_float_returns_none(self) -> None:
        assert _safe_int(3.14) is None

    def test_none_returns_none(self) -> None:
        assert _safe_int(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _safe_int("") is None

    def test_invalid_string_returns_none(self) -> None:
        assert _safe_int("abc") is None

    def test_negative_integer(self) -> None:
        assert _safe_int(-5) == -5


# ---------------------------------------------------------------------------
# _as_str
# ---------------------------------------------------------------------------

class TestAsStr:
    def test_string_passthrough(self) -> None:
        assert _as_str("hello") == "hello"

    def test_whitespace_stripped(self) -> None:
        assert _as_str("  hello  ") == "hello"

    def test_none_returns_empty(self) -> None:
        assert _as_str(None) == ""

    def test_integer_returns_empty(self) -> None:
        assert _as_str(42) == ""

    def test_empty_string_returns_empty(self) -> None:
        assert _as_str("") == ""


# ---------------------------------------------------------------------------
# _as_list
# ---------------------------------------------------------------------------

class TestAsList:
    def test_list_passthrough(self) -> None:
        result = _as_list(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_string_becomes_single_item(self) -> None:
        assert _as_list("single") == ["single"]

    def test_none_returns_empty(self) -> None:
        assert _as_list(None) == []

    def test_empty_string_returns_empty(self) -> None:
        assert _as_list("") == []

    def test_whitespace_items_excluded(self) -> None:
        result = _as_list(["a", "   ", "b"])
        assert result == ["a", "b"]

    def test_items_stripped(self) -> None:
        result = _as_list(["  hello  ", "  world  "])
        assert result == ["hello", "world"]

    def test_integer_returns_empty(self) -> None:
        assert _as_list(42) == []
