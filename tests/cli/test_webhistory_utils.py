"""Tests for pure helper functions in sources/captures/webhistory.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lynchpin.sources.web import (
    _history_files,
    _history_files_signature,
    _normalize_domain,
    _parse_csv_dt,
    _tokenize_topic,
    iter_gestalt_events,
    normalize_url,
    parse_webhistory_timestamp,
)
from lynchpin.sources.web_timestamps import (
    _parse_webhistory_slash_timestamp,
)
from lynchpin.sources.takeout_chrome import iter_takeout_chrome_visits


@pytest.fixture(autouse=True)
def _no_webhistory_materialization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", lambda *_args, **_kwargs: None)


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


def test_history_files_signature_accepts_positional_cache_args(tmp_path: Path) -> None:
    ndjson = tmp_path / "full_history.ndjson"
    ndjson.write_text('{"iso_time":"2026-05-01T00:00:00+00:00","url":"https://example.com"}\n', encoding="utf-8")

    assert _history_files_signature(None, ndjson)


def test_default_history_files_requires_canonical_ndjson(monkeypatch, tmp_path: Path) -> None:
    class Config:
        webhistory_ndjson = tmp_path / "missing_full_history.ndjson"
        webhistory_dir = tmp_path / "data"

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "segment.ndjson").write_text(
        '{"iso_time":"2026-05-01T00:00:00+00:00","url":"https://segment.example"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("lynchpin.sources.web.get_config", lambda: Config())

    with pytest.raises(FileNotFoundError, match="canonical webhistory NDJSON is missing"):
        _history_files()

    assert _history_files(root=tmp_path / "data") == [tmp_path / "data" / "segment.ndjson"]


def test_default_history_files_materializes_canonical_ndjson(monkeypatch, tmp_path: Path) -> None:
    calls = []
    ndjson = tmp_path / "full_history.ndjson"
    ndjson.write_text(
        '{"iso_time":"2026-05-01T00:00:00+00:00","url":"https://example.com"}\n',
        encoding="utf-8",
    )

    class Config:
        webhistory_ndjson = ndjson
        webhistory_dir = tmp_path / "data"

    monkeypatch.setattr("lynchpin.sources.web.get_config", lambda: Config())
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: calls.append((name, window)),
    )

    assert _history_files() == [ndjson]
    assert calls == [("webhistory", None)]

    calls.clear()
    root = tmp_path / "raw"
    root.mkdir()
    segment = root / "segment.ndjson"
    segment.write_text(
        '{"iso_time":"2026-05-01T00:00:00+00:00","url":"https://segment.example"}\n',
        encoding="utf-8",
    )

    assert _history_files(root=root) == [segment]
    assert calls == []

    assert _history_files(ndjson=segment) == [segment]
    assert calls == []


def test_takeout_chrome_reads_legacy_browser_history(tmp_path: Path) -> None:
    path = tmp_path / "BrowserHistory.json"
    path.write_text(
        json.dumps(
            {
                "Browser History": [
                    {
                        "title": "Example",
                        "url": "https://example.com",
                        "time_usec": 1_700_000_000_000_000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    visits = list(iter_takeout_chrome_visits(path))

    assert len(visits) == 1
    assert visits[0].url == "https://example.com"
    assert visits[0].timestamp.year == 2023


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
        assert visits[0].timestamp == datetime(
            2023, 11, 23, 16, 52, 21, 618000, tzinfo=timezone.utc
        )

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

    def test_jsonl_ts_file_supported(self, tmp_path) -> None:
        path = tmp_path / "webcache.jsonl"
        path.write_text(
            json.dumps(
                {
                    "url": "Visited: user@https://example.com/",
                    "ts": "2022-09-06T00:22:32.314865+00:00",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        visits = list(iter_gestalt_events(tmp_path))
        assert len(visits) == 1
        assert visits[0].timestamp == datetime(
            2022, 9, 6, 0, 22, 32, 314865, tzinfo=timezone.utc
        )

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

    def test_json_array_file_supported(self, tmp_path) -> None:
        path = tmp_path / "history.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "url": "https://example.com/1",
                        "title": "One",
                        "visitTime": 1742000000000,
                    },
                    {
                        "url": "https://example.com/2",
                        "title": "Two",
                        "visitTime": 1742000001000,
                    },
                ]
            ),
            encoding="utf-8",
        )
        visits = list(iter_gestalt_events(tmp_path))
        assert len(visits) == 2


# ---------------------------------------------------------------------------
# daily_browsing — logical-date bucketing (fix: was UTC .date())
# ---------------------------------------------------------------------------


def _make_ndjson_with_visits(tmp_path, visits_iso: list[str]) -> "Path":
    """Write an NDJSON file with visits at given ISO timestamps."""
    ndjson = tmp_path / "full_history.ndjson"
    lines = [
        json.dumps({"url": f"https://example.com/{i}", "title": f"Page {i}", "iso_time": ts})
        for i, ts in enumerate(visits_iso)
    ]
    ndjson.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ndjson


def test_daily_browsing_post_midnight_utc_visit_attributed_to_logical_day(tmp_path, monkeypatch):
    """A visit at 02:00 local on May 15 (before the 06:00 boundary) must be
    attributed to logical day May 14, not the calendar day of the UTC timestamp.

    Before the fix, daily_browsing used .date() on the UTC timestamp, so for a
    UTC+2 operator the visit stored as 2026-05-15T00:00Z would land on May 15.
    After the fix it uses logical_date → May 14.
    """
    from datetime import date as _date
    from lynchpin.sources.web import daily_browsing
    from lynchpin.core.parse import local_tz

    tz = local_tz()
    # 02:00 local on May 15 — before 06:00 boundary, logical day = May 14
    local_dt = datetime(2026, 5, 15, 2, 0, tzinfo=tz)
    utc_iso = local_dt.astimezone(timezone.utc).isoformat()

    ndjson = _make_ndjson_with_visits(tmp_path, [utc_iso])

    class FakeConfig:
        webhistory_ndjson = ndjson

    monkeypatch.setattr("lynchpin.sources.web.get_config", lambda: FakeConfig())

    result = daily_browsing(start=_date(2026, 5, 14), end=_date(2026, 5, 14))
    assert len(result) == 1, (
        "Post-midnight local visit (02:00 local May 15 = logical May 14) "
        "must appear on May 14, not May 15"
    )
    assert result[0].date == _date(2026, 5, 14)


def test_daily_browsing_post_midnight_visit_excluded_from_next_day_query(tmp_path, monkeypatch):
    """The same 02:00 local visit must NOT appear when querying only May 15."""
    from datetime import date as _date
    from lynchpin.sources.web import daily_browsing
    from lynchpin.core.parse import local_tz

    tz = local_tz()
    local_dt = datetime(2026, 5, 15, 2, 0, tzinfo=tz)
    utc_iso = local_dt.astimezone(timezone.utc).isoformat()

    ndjson = _make_ndjson_with_visits(tmp_path, [utc_iso])

    class FakeConfig:
        webhistory_ndjson = ndjson

    monkeypatch.setattr("lynchpin.sources.web.get_config", lambda: FakeConfig())

    result = daily_browsing(start=_date(2026, 5, 15), end=_date(2026, 5, 15))
    assert result == [], (
        "Post-midnight visit (logical May 14) must not appear when querying May 15"
    )


def test_daily_browsing_normal_daytime_visit_unaffected(tmp_path, monkeypatch):
    """Visits during normal hours (12:00 local) still land on their calendar day."""
    from datetime import date as _date
    from lynchpin.sources.web import daily_browsing
    from lynchpin.core.parse import local_tz

    tz = local_tz()
    local_dt = datetime(2026, 5, 15, 12, 0, tzinfo=tz)
    utc_iso = local_dt.astimezone(timezone.utc).isoformat()

    ndjson = _make_ndjson_with_visits(tmp_path, [utc_iso])

    class FakeConfig:
        webhistory_ndjson = ndjson

    monkeypatch.setattr("lynchpin.sources.web.get_config", lambda: FakeConfig())

    result = daily_browsing(start=_date(2026, 5, 15), end=_date(2026, 5, 15))
    assert len(result) == 1
    assert result[0].date == _date(2026, 5, 15)


def test_daily_browsing_and_domain_breakdown_share_source_index(tmp_path, monkeypatch):
    from datetime import date as _date
    from lynchpin.sources.web import daily_browsing, domain_breakdown

    ndjson = tmp_path / "full_history.ndjson"
    ndjson.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "url": "https://example.com/a",
                        "title": "A",
                        "iso_time": "2026-05-15T10:00:00+00:00",
                    }
                ),
                json.dumps(
                    {
                        "url": "https://example.com/b",
                        "title": "B",
                        "iso_time": "2026-05-15T10:01:00+00:00",
                    }
                ),
                json.dumps(
                    {
                        "url": "https://github.com/org/repo",
                        "title": "Repo",
                        "iso_time": "2026-05-15T10:02:00+00:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeConfig:
        webhistory_ndjson = ndjson

    monkeypatch.setattr("lynchpin.sources.web.get_config", lambda: FakeConfig())

    days = daily_browsing(start=_date(2026, 5, 15), end=_date(2026, 5, 15))
    assert [(day.date, day.visit_count, day.unique_domains) for day in days] == [
        (_date(2026, 5, 15), 3, 2)
    ]
    assert domain_breakdown(start=_date(2026, 5, 15), end=_date(2026, 5, 15))[:2] == [
        ("example.com", 2, 0.6667),
        ("github.com", 1, 0.3333),
    ]


def test_daily_web_aggregates_forward_requested_materialization_window(tmp_path, monkeypatch):
    from datetime import date as _date
    from lynchpin.sources.web import daily_browsing, domain_breakdown

    calls = []
    ndjson = _make_ndjson_with_visits(tmp_path, ["2026-05-15T10:00:00+00:00"])

    class FakeConfig:
        webhistory_ndjson = ndjson

    monkeypatch.setattr("lynchpin.sources.web.get_config", lambda: FakeConfig())
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: calls.append((name, window)),
    )

    daily_browsing(start=_date(2026, 5, 15), end=_date(2026, 5, 15))
    assert calls[-1] == ("webhistory", (_date(2026, 5, 15), _date(2026, 5, 16)))

    domain_breakdown(start=_date(2026, 5, 15), end=_date(2026, 5, 15))
    assert calls[-1] == ("webhistory", (_date(2026, 5, 15), _date(2026, 5, 16)))


def test_daily_web_aggregates_can_skip_ensure(tmp_path, monkeypatch):
    from datetime import date as _date
    from lynchpin.sources.web import daily_browsing, domain_breakdown

    ndjson = _make_ndjson_with_visits(tmp_path, ["2026-05-15T10:00:00+00:00"])

    class FakeConfig:
        webhistory_ndjson = ndjson
        cache_dir = tmp_path

    def fail_ensure(*_args, **_kwargs):
        raise AssertionError("pre-ensured reads must not materialize again")

    monkeypatch.setattr("lynchpin.sources.web.get_config", lambda: FakeConfig())
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fail_ensure)

    assert daily_browsing(
        start=_date(2026, 5, 15),
        end=_date(2026, 5, 15),
        ensure=False,
    )[0].visit_count == 1
    assert domain_breakdown(
        start=_date(2026, 5, 15),
        end=_date(2026, 5, 15),
        ensure=False,
    )[0][0] == "example.com"
