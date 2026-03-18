"""Tests for pure helper functions in sources/captures/webhistory.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from lynchpin.sources.captures.webhistory import (
    _month_key_from_dt,
    _month_key_in_range,
    _normalize_domain,
    _parse_webhistory_csv_dt,
    _parse_webhistory_json_dt,
    _tokenize,
    iter_gestalt_events,
)


# ---------------------------------------------------------------------------
# _parse_webhistory_csv_dt
# ---------------------------------------------------------------------------

class TestParseWebhistoryCsvDt:
    def test_slash_format_parsed(self) -> None:
        result = _parse_webhistory_csv_dt("03/17/2026", "14:30:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 17

    def test_time_components_preserved(self) -> None:
        result = _parse_webhistory_csv_dt("03/17/2026", "14:30:00")
        assert result.hour == 14
        assert result.minute == 30

    def test_two_digit_year_accepted(self) -> None:
        result = _parse_webhistory_csv_dt("03/17/26", "10:00:00")
        assert result is not None

    def test_result_is_utc(self) -> None:
        result = _parse_webhistory_csv_dt("03/17/2026", "10:00:00")
        assert result.tzinfo == timezone.utc

    def test_empty_date_returns_none(self) -> None:
        assert _parse_webhistory_csv_dt("", "10:00:00") is None

    def test_empty_time_returns_none(self) -> None:
        assert _parse_webhistory_csv_dt("03/17/2026", "") is None

    def test_invalid_format_returns_none(self) -> None:
        assert _parse_webhistory_csv_dt("2026-03-17", "10:00:00") is None

    def test_time_without_seconds_accepted(self) -> None:
        result = _parse_webhistory_csv_dt("03/17/2026", "14:30")
        assert result is not None


# ---------------------------------------------------------------------------
# _parse_webhistory_json_dt
# ---------------------------------------------------------------------------

class TestParseWebhistoryJsonDt:
    def test_millisecond_timestamp_parsed(self) -> None:
        # 0 epoch → 1970-01-01T00:00:00Z
        result = _parse_webhistory_json_dt(0)
        assert result is not None
        assert result.year == 1970

    def test_large_ms_timestamp(self) -> None:
        # 1742000000000 ms ~ 2025
        result = _parse_webhistory_json_dt(1742000000000)
        assert result is not None
        assert result.year >= 2025

    def test_iso_string_z_suffix(self) -> None:
        result = _parse_webhistory_json_dt("2026-03-17T10:00:00Z")
        assert result is not None
        assert result.year == 2026

    def test_iso_string_with_offset(self) -> None:
        result = _parse_webhistory_json_dt("2026-03-17T10:00:00+00:00")
        assert result is not None

    def test_numeric_string_timestamp(self) -> None:
        result = _parse_webhistory_json_dt("1773717025870.549")
        assert result is not None
        assert result.year == 2026

    def test_nanosecond_timestamp(self) -> None:
        result = _parse_webhistory_json_dt(1773717025870548992)
        assert result is not None
        assert result.year == 2026

    def test_none_returns_none(self) -> None:
        assert _parse_webhistory_json_dt(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_webhistory_json_dt("") is None

    def test_invalid_string_returns_none(self) -> None:
        assert _parse_webhistory_json_dt("not a date") is None

    def test_result_is_utc(self) -> None:
        result = _parse_webhistory_json_dt("2026-03-17T10:00:00Z")
        assert result.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# _month_key_from_dt
# ---------------------------------------------------------------------------

class TestMonthKeyFromDt:
    def test_basic_format(self) -> None:
        dt = datetime(2026, 3, 17, tzinfo=timezone.utc)
        assert _month_key_from_dt(dt) == "2026-03"

    def test_zero_padded_month(self) -> None:
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert _month_key_from_dt(dt) == "2026-01"

    def test_december(self) -> None:
        dt = datetime(2026, 12, 31, tzinfo=timezone.utc)
        assert _month_key_from_dt(dt) == "2026-12"

    def test_year_preserved(self) -> None:
        dt = datetime(2025, 6, 15, tzinfo=timezone.utc)
        assert _month_key_from_dt(dt).startswith("2025-")


# ---------------------------------------------------------------------------
# _month_key_in_range
# ---------------------------------------------------------------------------

class TestMonthKeyInRange:
    def test_month_within_range(self) -> None:
        assert _month_key_in_range("2026-03", "2026-01", "2026-06") is True

    def test_month_at_start(self) -> None:
        assert _month_key_in_range("2026-01", "2026-01", "2026-06") is True

    def test_month_at_end(self) -> None:
        assert _month_key_in_range("2026-06", "2026-01", "2026-06") is True

    def test_month_before_range(self) -> None:
        assert _month_key_in_range("2025-12", "2026-01", "2026-06") is False

    def test_month_after_range(self) -> None:
        assert _month_key_in_range("2026-07", "2026-01", "2026-06") is False


# ---------------------------------------------------------------------------
# _normalize_domain
# ---------------------------------------------------------------------------

class TestNormalizeDomain:
    def test_www_stripped(self) -> None:
        assert _normalize_domain("www.github.com") == "github.com"

    def test_no_www_unchanged(self) -> None:
        assert _normalize_domain("github.com") == "github.com"

    def test_port_stripped(self) -> None:
        assert _normalize_domain("localhost:8080") == "localhost"

    def test_uppercased_lowercased(self) -> None:
        assert _normalize_domain("GitHub.COM") == "github.com"

    def test_leading_whitespace_stripped(self) -> None:
        assert _normalize_domain("  github.com  ") == "github.com"

    def test_www_with_port_both_stripped(self) -> None:
        assert _normalize_domain("www.example.com:443") == "example.com"


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------

class TestTokenize:
    def test_simple_words(self) -> None:
        assert _tokenize("hello world") == ["hello", "world"]

    def test_lowercased(self) -> None:
        assert _tokenize("Hello World") == ["hello", "world"]

    def test_punctuation_excluded(self) -> None:
        result = _tokenize("foo, bar! baz.")
        assert "," not in result
        assert "foo" in result

    def test_empty_string_returns_empty(self) -> None:
        assert _tokenize("") == []

    def test_numbers_included(self) -> None:
        assert "42" in _tokenize("item 42")

    def test_underscore_identifier_kept_as_one_token(self) -> None:
        # re.findall(r"[\w]+") treats underscore as a word char — no split
        result = _tokenize("some_identifier")
        assert result == ["some_identifier"]


class TestIterGestaltEvents:
    def test_edge_style_csv_file_supported(self, tmp_path) -> None:
        path = tmp_path / "history.csv"
        path.write_text(
            "DateTime,NavigatedToUrl,PageTitle\n"
            "2023-11-23T16:52:21.618Z,https://www.qutebrowser.org/doc/changelog.html,Change Log | qutebrowser\n",
            encoding="utf-8",
        )

        visits = list(iter_gestalt_events(tmp_path))

        assert len(visits) == 1
        assert visits[0].url == "https://www.qutebrowser.org/doc/changelog.html"
        assert visits[0].title == "Change Log | qutebrowser"
        assert visits[0].timestamp == datetime(2023, 11, 23, 16, 52, 21, 618000, tzinfo=timezone.utc)

    def test_jsonl_visit_time_file_supported(self, tmp_path) -> None:
        path = tmp_path / "history.jsonl"
        path.write_text(
            json.dumps(
                {
                    "url": "https://example.com/path?utm_source=test",
                    "title": "Example",
                    "visit_time": "2026-03-17T10:00:00+00:00",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        visits = list(iter_gestalt_events(tmp_path))

        assert len(visits) == 1
        assert visits[0].timestamp == datetime(2026, 3, 17, 10, 0, tzinfo=timezone.utc)
        assert visits[0].url == "https://example.com/path?utm_source=test"

    def test_ndjson_json_file_supported(self, tmp_path) -> None:
        path = tmp_path / "history.json"
        path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "url": "https://example.com/a",
                            "title": "A",
                            "visit_time": "2026-03-17T10:00:00+00:00",
                        }
                    ),
                    json.dumps(
                        {
                            "url": "https://example.com/b",
                            "title": "B",
                            "visitTime": 1773717025870.549,
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        visits = list(iter_gestalt_events(tmp_path))

        assert len(visits) == 2
        assert visits[0].url == "https://example.com/a"
        assert visits[1].timestamp.year == 2026
