"""Tests for lynchpin.views.warehouse pure utility functions."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

# warehouse.py imports duckdb at module level; skip entire file if unavailable
pytest.importorskip("duckdb", exc_type=ImportError)

from lynchpin.views.warehouse.core import _json_dumps, _maybe_limit, _parse_dt  # noqa: E402


# ---------------------------------------------------------------------------
# _parse_dt
# ---------------------------------------------------------------------------

class TestParseDt:
    def test_none_returns_none(self) -> None:
        assert _parse_dt(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_dt("") is None

    def test_whitespace_string_returns_none(self) -> None:
        assert _parse_dt("   ") is None

    def test_datetime_object_returned_directly(self) -> None:
        dt = datetime(2026, 3, 17, 10, 0, 0, tzinfo=timezone.utc)
        assert _parse_dt(dt) is dt

    def test_date_object_converted_to_datetime(self) -> None:
        d = date(2026, 3, 17)
        result = _parse_dt(d)
        assert isinstance(result, datetime)
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 17
        assert result.tzinfo == timezone.utc

    def test_valid_iso_string_parsed(self) -> None:
        result = _parse_dt("2026-03-17T10:00:00+00:00")
        assert isinstance(result, datetime)
        assert result.year == 2026

    def test_z_suffix_handled(self) -> None:
        result = _parse_dt("2026-03-17T10:00:00Z")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_invalid_string_returns_none(self) -> None:
        assert _parse_dt("not-a-datetime") is None

    def test_integer_coerced_and_parsed_if_valid_iso(self) -> None:
        # Non-string non-None non-date goes through str() → probably not valid ISO
        result = _parse_dt(12345)
        assert result is None

    def test_date_subclass_is_datetime_not_converted(self) -> None:
        # datetime is a subclass of date; the guard is "not isinstance(value, datetime)"
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = _parse_dt(dt)
        assert isinstance(result, datetime)
        assert result.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# _json_dumps
# ---------------------------------------------------------------------------

class TestJsonDumps:
    def test_dict_serialized(self) -> None:
        result = _json_dumps({"key": "value"})
        parsed = json.loads(result)
        assert parsed == {"key": "value"}

    def test_list_serialized(self) -> None:
        result = _json_dumps([1, 2, 3])
        assert json.loads(result) == [1, 2, 3]

    def test_none_serialized(self) -> None:
        assert _json_dumps(None) == "null"

    def test_non_serializable_uses_str_fallback(self) -> None:
        # datetime is not JSON serializable by default → default=str converts it
        dt = datetime(2026, 3, 17, 10, 0, 0, tzinfo=timezone.utc)
        result = _json_dumps({"ts": dt})
        parsed = json.loads(result)
        assert isinstance(parsed["ts"], str)
        assert "2026" in parsed["ts"]

    def test_nested_structure(self) -> None:
        payload = {"a": {"b": [1, 2, {"c": True}]}}
        result = _json_dumps(payload)
        assert json.loads(result) == payload

    def test_empty_dict(self) -> None:
        assert _json_dumps({}) == "{}"

    def test_empty_list(self) -> None:
        assert _json_dumps([]) == "[]"


# ---------------------------------------------------------------------------
# _maybe_limit
# ---------------------------------------------------------------------------

class TestMaybeLimit:
    def test_none_limit_yields_all_items(self) -> None:
        result = list(_maybe_limit([1, 2, 3, 4, 5], None))
        assert result == [1, 2, 3, 4, 5]

    def test_limit_zero_yields_nothing(self) -> None:
        result = list(_maybe_limit([1, 2, 3], 0))
        assert result == []

    def test_limit_less_than_length_truncates(self) -> None:
        result = list(_maybe_limit([1, 2, 3, 4, 5], 3))
        assert result == [1, 2, 3]

    def test_limit_greater_than_length_yields_all(self) -> None:
        result = list(_maybe_limit([1, 2], 10))
        assert result == [1, 2]

    def test_limit_equals_length_yields_all(self) -> None:
        result = list(_maybe_limit([1, 2, 3], 3))
        assert result == [1, 2, 3]

    def test_empty_iterator_yields_nothing(self) -> None:
        result = list(_maybe_limit([], 5))
        assert result == []

    def test_generator_consumed_lazily(self) -> None:
        # Ensure it works with generators, not just lists
        result = list(_maybe_limit((x for x in range(10)), 4))
        assert result == [0, 1, 2, 3]

    def test_none_limit_with_empty_iterator(self) -> None:
        result = list(_maybe_limit(iter([]), None))
        assert result == []
