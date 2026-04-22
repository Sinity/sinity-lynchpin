"""Tests for pure helper functions in sources/captures/webhistory.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone


from lynchpin.sources.web import (
    _normalize_domain,
    _parse_csv_dt,
    _tokenize_topic,
    normalize_url,
    iter_gestalt_events,
)
from lynchpin.sources.web import (
    parse_webhistory_timestamp,
    _parse_webhistory_slash_timestamp,
)


# ---------------------------------------------------------------------------
# _parse_csv_dt (Chrome CSV local-time parsing)
# ---------------------------------------------------------------------------

class TestParseCsvDt:
    def test_slash_format_parsed(self) -> None:
        result = _parse_csv_dt({"date": "03/17/2026", "time": "14:30:00"})
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 17

    def test_time_components_converted_from_local(self) -> None:
        # Chrome CSV timestamps are local time (Europe/Warsaw).
        # March 17 2026 is CET (UTC+1), so 14:30 local = 13:30 UTC.
        result = _parse_csv_dt({"date": "03/17/2026", "time": "14:30:00"})
        assert result.hour == 13
        assert result.minute == 30

    def test_summer_time_conversion(self) -> None:
        # July is CEST (UTC+2), so 14:00 local = 12:00 UTC.
        result = _parse_csv_dt({"date": "07/15/2025", "time": "14:00:00"})
        assert result.hour == 12

    def test_two_digit_year_accepted(self) -> None:
        result = _parse_csv_dt({"date": "03/17/26", "time": "10:00:00"})
        assert result is not None

    def test_result_is_utc(self) -> None:
        result = _parse_csv_dt({"date": "03/17/2026", "time": "10:00:00"})
        assert result.tzinfo == timezone.utc

    def test_empty_date_returns_none(self) -> None:
        assert _parse_csv_dt({"date": "", "time": "10:00:00"}) is None

    def test_empty_time_returns_none(self) -> None:
        assert _parse_csv_dt({"date": "03/17/2026", "time": ""}) is None

    def test_iso_date_falls_through_to_field_lookup(self) -> None:
        # ISO date in "date" column doesn't match slash format but the
        # fallback finds it via WEBHISTORY_TIMESTAMP_FIELDS → parse succeeds.
        result = _parse_csv_dt({"date": "2026-03-17", "time": "10:00:00"})
        assert result is not None

    def test_truly_invalid_returns_none(self) -> None:
        assert _parse_csv_dt({"date": "not-a-date", "time": "not-a-time"}) is None

    def test_time_without_seconds_accepted(self) -> None:
        result = _parse_csv_dt({"date": "03/17/2026", "time": "14:30"})
        assert result is not None

    def test_edge_datetime_column(self) -> None:
        result = _parse_csv_dt({"DateTime": "2023-11-23T16:52:21.618Z"})
        assert result is not None
        assert result.year == 2023

    def test_fallback_to_timestamp_fields(self) -> None:
        result = _parse_csv_dt({"iso_time": "2026-03-17T10:00:00+00:00"})
        assert result is not None


# ---------------------------------------------------------------------------
# _parse_webhistory_slash_timestamp (from webhistory_common)
# ---------------------------------------------------------------------------

class TestSlashTimestamp:
    def test_local_to_utc_winter(self) -> None:
        result = _parse_webhistory_slash_timestamp("12/04/2025 21:35:53")
        assert result.hour == 20  # CET = UTC+1

    def test_local_to_utc_summer(self) -> None:
        result = _parse_webhistory_slash_timestamp("07/15/2025 14:00:00")
        assert result.hour == 12  # CEST = UTC+2


# ---------------------------------------------------------------------------
# parse_webhistory_timestamp (JSON/numeric/ISO)
# ---------------------------------------------------------------------------

class TestParseWebhistoryTimestamp:
    def test_millisecond_timestamp_parsed(self) -> None:
        result = parse_webhistory_timestamp(0)
        assert result is not None
        assert result.year == 1970

    def test_large_ms_timestamp(self) -> None:
        result = parse_webhistory_timestamp(1742000000000)
        assert result is not None
        assert result.year >= 2025

    def test_iso_string_z_suffix(self) -> None:
        result = parse_webhistory_timestamp("2026-03-17T10:00:00Z")
        assert result is not None
        assert result.year == 2026

    def test_iso_string_with_offset(self) -> None:
        result = parse_webhistory_timestamp("2026-03-17T10:00:00+00:00")
        assert result is not None

    def test_numeric_string_timestamp(self) -> None:
        result = parse_webhistory_timestamp("1773717025870.549")
        assert result is not None
        assert result.year == 2026

    def test_nanosecond_timestamp(self) -> None:
        result = parse_webhistory_timestamp(1773717025870548992)
        assert result is not None
        assert result.year == 2026

    def test_none_returns_none(self) -> None:
        assert parse_webhistory_timestamp(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_webhistory_timestamp("") is None

    def test_invalid_string_returns_none(self) -> None:
        assert parse_webhistory_timestamp("not a date") is None

    def test_result_is_utc(self) -> None:
        result = parse_webhistory_timestamp("2026-03-17T10:00:00Z")
        assert result.tzinfo == timezone.utc


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

    def test_w_prefix_domains_not_corrupted(self) -> None:
        assert _normalize_domain("www.wikipedia.org") == "wikipedia.org"
        assert _normalize_domain("www.wired.com") == "wired.com"
        assert _normalize_domain("www.walmart.com") == "walmart.com"


# ---------------------------------------------------------------------------
# normalize_url
# ---------------------------------------------------------------------------

class TestNormalizeUrl:
    def test_tracking_params_stripped(self) -> None:
        result = normalize_url("https://example.com/path?utm_source=test&keep=1")
        assert "utm_source" not in result
        assert "keep=1" in result

    def test_www_stripped(self) -> None:
        assert "www." not in normalize_url("https://www.example.com/path")

    def test_non_http_scheme_preserved(self) -> None:
        url = "chrome-extension://abcdef/popup.html"
        assert normalize_url(url) == url

    def test_file_scheme_preserved(self) -> None:
        url = "file:///home/user/doc.html"
        assert normalize_url(url) == url

    def test_youtu_be_normalized(self) -> None:
        result = normalize_url("https://youtu.be/abc123")
        assert "youtube.com/watch" in result
        assert "v=abc123" in result

    def test_youtu_be_no_duplicate_v(self) -> None:
        result = normalize_url("https://youtu.be/abc123?v=existing")
        assert result.count("v=") == 1


# ---------------------------------------------------------------------------
# _tokenize_topic
# ---------------------------------------------------------------------------

class TestTokenizeTopic:
    def test_stopwords_filtered(self) -> None:
        result = _tokenize_topic("the quick brown fox")
        assert "the" not in result
        assert "quick" in result
        assert "brown" in result

    def test_short_tokens_filtered(self) -> None:
        result = _tokenize_topic("a ab abc abcd")
        assert "abc" in result
        assert "ab" not in result

    def test_digits_filtered(self) -> None:
        result = _tokenize_topic("item 42 test")
        assert "42" not in result
        assert "item" in result


# ---------------------------------------------------------------------------
# iter_gestalt_events
# ---------------------------------------------------------------------------

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
            json.dumps({
                "url": "https://example.com/path?utm_source=test",
                "title": "Example",
                "visit_time": "2026-03-17T10:00:00+00:00",
            }) + "\n",
            encoding="utf-8",
        )
        visits = list(iter_gestalt_events(tmp_path))
        assert len(visits) == 1
        assert visits[0].timestamp == datetime(2026, 3, 17, 10, 0, tzinfo=timezone.utc)

    def test_ndjson_json_file_supported(self, tmp_path) -> None:
        path = tmp_path / "history.json"
        path.write_text(
            "\n".join([
                json.dumps({
                    "url": "https://example.com/a",
                    "title": "A",
                    "visit_time": "2026-03-17T10:00:00+00:00",
                }),
                json.dumps({
                    "url": "https://example.com/b",
                    "title": "B",
                    "visitTime": 1773717025870.549,
                }),
            ]) + "\n",
            encoding="utf-8",
        )
        visits = list(iter_gestalt_events(tmp_path))
        assert len(visits) == 2
        assert visits[0].url == "https://example.com/a"
        assert visits[1].timestamp.year == 2026

    def test_json_array_file_supported(self, tmp_path) -> None:
        path = tmp_path / "history.json"
        path.write_text(
            json.dumps([
                {"url": "https://example.com/1", "title": "One", "visitTime": 1742000000000},
                {"url": "https://example.com/2", "title": "Two", "visitTime": 1742000001000},
            ]),
            encoding="utf-8",
        )
        visits = list(iter_gestalt_events(tmp_path))
        assert len(visits) == 2
