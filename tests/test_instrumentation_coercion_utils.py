"""Tests for type-coercion and parsing helpers in sources/captures/instrumentation.py."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lynchpin.sources.captures.instrumentation import (
    _duration_between,
    _parse_iso_datetime,
    _session_time_from_id,
    _to_bool,
    _to_float,
    _to_int,
    _to_text,
)

_UTC = timezone.utc


# ---------------------------------------------------------------------------
# _to_int
# ---------------------------------------------------------------------------

class TestToInt:
    def test_none_returns_none(self) -> None:
        assert _to_int(None) is None

    def test_integer_returned(self) -> None:
        assert _to_int(42) == 42

    def test_string_integer_parsed(self) -> None:
        assert _to_int("99") == 99

    def test_float_truncated(self) -> None:
        assert _to_int(3.9) == 3

    def test_bool_true_is_one(self) -> None:
        assert _to_int(True) == 1

    def test_bool_false_is_zero(self) -> None:
        assert _to_int(False) == 0

    def test_invalid_string_returns_none(self) -> None:
        assert _to_int("not-a-number") is None

    def test_empty_string_returns_none(self) -> None:
        assert _to_int("") is None

    def test_returns_int_type(self) -> None:
        result = _to_int(5)
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# _to_float
# ---------------------------------------------------------------------------

class TestToFloat:
    def test_none_returns_none(self) -> None:
        assert _to_float(None) is None

    def test_float_returned(self) -> None:
        assert _to_float(3.14) == 3.14

    def test_integer_converted(self) -> None:
        assert _to_float(5) == 5.0

    def test_string_parsed(self) -> None:
        assert _to_float("2.5") == 2.5

    def test_invalid_string_returns_none(self) -> None:
        assert _to_float("not-a-float") is None

    def test_empty_string_returns_none(self) -> None:
        assert _to_float("") is None

    def test_returns_float_type(self) -> None:
        result = _to_float(1)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# _to_bool
# ---------------------------------------------------------------------------

class TestToBool:
    def test_none_returns_none(self) -> None:
        assert _to_bool(None) is None

    def test_true_returned(self) -> None:
        assert _to_bool(True) is True

    def test_false_returned(self) -> None:
        assert _to_bool(False) is False

    def test_integer_one_is_true(self) -> None:
        assert _to_bool(1) is True

    def test_integer_zero_is_false(self) -> None:
        assert _to_bool(0) is False

    def test_string_true_recognized(self) -> None:
        assert _to_bool("true") is True

    def test_string_yes_recognized(self) -> None:
        assert _to_bool("yes") is True

    def test_string_1_recognized(self) -> None:
        assert _to_bool("1") is True

    def test_string_false_recognized(self) -> None:
        assert _to_bool("false") is False

    def test_string_no_recognized(self) -> None:
        assert _to_bool("no") is False

    def test_string_0_recognized(self) -> None:
        assert _to_bool("0") is False

    def test_unknown_string_returns_none(self) -> None:
        assert _to_bool("maybe") is None

    def test_empty_string_returns_none(self) -> None:
        assert _to_bool("") is None

    def test_case_insensitive(self) -> None:
        assert _to_bool("TRUE") is True
        assert _to_bool("FALSE") is False


# ---------------------------------------------------------------------------
# _to_text
# ---------------------------------------------------------------------------

class TestToText:
    def test_none_returns_none(self) -> None:
        assert _to_text(None) is None

    def test_normal_string_returned(self) -> None:
        assert _to_text("hello") == "hello"

    def test_whitespace_stripped(self) -> None:
        assert _to_text("  hello  ") == "hello"

    def test_empty_string_returns_none(self) -> None:
        assert _to_text("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert _to_text("   ") is None

    def test_integer_converted(self) -> None:
        assert _to_text(42) == "42"


# ---------------------------------------------------------------------------
# _parse_iso_datetime
# ---------------------------------------------------------------------------

class TestParseIsoDt:
    def test_none_returns_none(self) -> None:
        assert _parse_iso_datetime(None) is None

    def test_empty_returns_none(self) -> None:
        assert _parse_iso_datetime("") is None

    def test_whitespace_returns_none(self) -> None:
        assert _parse_iso_datetime("   ") is None

    def test_invalid_returns_none(self) -> None:
        assert _parse_iso_datetime("not-a-date") is None

    def test_iso_offset_parsed(self) -> None:
        result = _parse_iso_datetime("2026-03-17T10:00:00+00:00")
        assert isinstance(result, datetime)
        assert result.year == 2026

    def test_z_suffix_normalized(self) -> None:
        result = _parse_iso_datetime("2026-03-17T10:00:00Z")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_naive_iso_accepted(self) -> None:
        result = _parse_iso_datetime("2026-03-17T10:00:00")
        assert isinstance(result, datetime)


# ---------------------------------------------------------------------------
# _duration_between
# ---------------------------------------------------------------------------

class TestDurationBetween:
    def test_one_hour_duration(self) -> None:
        result = _duration_between(
            "2026-03-17T10:00:00+00:00",
            "2026-03-17T11:00:00+00:00",
        )
        assert result is not None
        assert abs(result - 3600.0) < 1.0

    def test_invalid_start_returns_none(self) -> None:
        assert _duration_between("not-a-date", "2026-03-17T11:00:00+00:00") is None

    def test_invalid_end_returns_none(self) -> None:
        assert _duration_between("2026-03-17T10:00:00+00:00", "not-a-date") is None

    def test_none_start_returns_none(self) -> None:
        assert _duration_between(None, "2026-03-17T11:00:00+00:00") is None

    def test_end_before_start_returns_zero(self) -> None:
        result = _duration_between(
            "2026-03-17T11:00:00+00:00",
            "2026-03-17T10:00:00+00:00",
        )
        assert result == 0.0

    def test_same_time_returns_zero(self) -> None:
        result = _duration_between(
            "2026-03-17T10:00:00+00:00",
            "2026-03-17T10:00:00+00:00",
        )
        assert result == 0.0


# ---------------------------------------------------------------------------
# _session_time_from_id
# ---------------------------------------------------------------------------

class TestSessionTimeFromId:
    def test_empty_returns_none(self) -> None:
        assert _session_time_from_id("") is None

    def test_date_underscore_time_format(self) -> None:
        # "2026-03-17_10-30-00" → ISO datetime string
        result = _session_time_from_id("2026-03-17_10-30-00")
        assert result is not None
        assert "2026-03-17" in result

    def test_compact_z_format(self) -> None:
        # ends in YYYYMMDDTHHMMSSZ
        result = _session_time_from_id("session_20260317T103000Z")
        assert result is not None

    def test_random_id_returns_none(self) -> None:
        assert _session_time_from_id("abc-def-ghi") is None

    def test_13_digit_ms_epoch_at_end(self) -> None:
        # 13-digit epoch at end of id
        epoch_ms = "1742212800000"  # some valid epoch ms
        result = _session_time_from_id(f"session_{epoch_ms}")
        assert result is not None

    def test_returns_string(self) -> None:
        result = _session_time_from_id("2026-03-17_10-30-00")
        if result is not None:
            assert isinstance(result, str)
