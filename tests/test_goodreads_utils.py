"""Tests for pure helper functions in sources/exports/goodreads.py."""

from __future__ import annotations

from datetime import datetime

import pytest

from lynchpin.sources.exports.goodreads import (
    _normalize_isbn,
    _parse_date,
    _parse_float,
    _parse_int,
    _split_csv_field,
)


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_slash_format(self) -> None:
        result = _parse_date("2023/11/15")
        assert result.year == 2023
        assert result.month == 11
        assert result.day == 15

    def test_dash_format(self) -> None:
        result = _parse_date("2023-11-15")
        assert result is not None
        assert result.year == 2023

    def test_empty_returns_none(self) -> None:
        assert _parse_date("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert _parse_date("   ") is None

    def test_invalid_format_returns_none(self) -> None:
        assert _parse_date("15/11/2023") is None

    def test_whitespace_stripped(self) -> None:
        result = _parse_date("  2023/11/15  ")
        assert result is not None
        assert result.year == 2023


# ---------------------------------------------------------------------------
# _parse_int
# ---------------------------------------------------------------------------

class TestParseInt:
    def test_none_returns_none(self) -> None:
        assert _parse_int(None) is None

    def test_integer_string_parsed(self) -> None:
        assert _parse_int("42") == 42

    def test_whitespace_stripped(self) -> None:
        assert _parse_int("  17  ") == 17

    def test_empty_returns_none(self) -> None:
        assert _parse_int("") is None

    def test_invalid_string_returns_none(self) -> None:
        assert _parse_int("not a number") is None

    def test_negative_integer(self) -> None:
        assert _parse_int("-5") == -5


# ---------------------------------------------------------------------------
# _parse_float
# ---------------------------------------------------------------------------

class TestParseFloat:
    def test_none_returns_none(self) -> None:
        assert _parse_float(None) is None

    def test_float_string_parsed(self) -> None:
        assert _parse_float("3.14") == pytest.approx(3.14)

    def test_empty_returns_none(self) -> None:
        assert _parse_float("") is None

    def test_whitespace_stripped(self) -> None:
        assert _parse_float("  4.5  ") == pytest.approx(4.5)

    def test_invalid_string_returns_none(self) -> None:
        assert _parse_float("not a float") is None

    def test_integer_string_accepted(self) -> None:
        assert _parse_float("5") == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# _split_csv_field
# ---------------------------------------------------------------------------

class TestSplitCsvField:
    def test_simple_items(self) -> None:
        assert _split_csv_field("a, b, c") == ["a", "b", "c"]

    def test_single_item(self) -> None:
        assert _split_csv_field("rust") == ["rust"]

    def test_empty_returns_empty(self) -> None:
        assert _split_csv_field("") == []

    def test_whitespace_stripped(self) -> None:
        result = _split_csv_field("  alpha  ,  beta  ")
        assert result == ["alpha", "beta"]

    def test_blank_items_excluded(self) -> None:
        result = _split_csv_field("a,,b")
        assert result == ["a", "b"]


# ---------------------------------------------------------------------------
# _normalize_isbn
# ---------------------------------------------------------------------------

class TestNormalizeIsbn:
    def test_plain_isbn_unchanged(self) -> None:
        assert _normalize_isbn("9781234567890") == "9781234567890"

    def test_excel_double_quote_escape_stripped(self) -> None:
        # Excel wraps as ="9781234567890"
        assert _normalize_isbn('="9781234567890"') == "9781234567890"

    def test_excel_single_quote_escape_stripped(self) -> None:
        assert _normalize_isbn("='9781234567890'") == "9781234567890"

    def test_leading_trailing_whitespace_stripped(self) -> None:
        assert _normalize_isbn("  1234567890  ") == "1234567890"

    def test_empty_returns_empty(self) -> None:
        assert _normalize_isbn("") == ""
