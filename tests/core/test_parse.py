"""Tests for core/parse.py."""

from datetime import date, datetime, timezone
from lynchpin.core.parse import (
    parse_date, parse_int, parse_float, month_key, in_month_range, iter_dates,
    in_date_range, parse_datetime, as_local,
)


def test_parse_date_iso():
    assert parse_date("2026-03-15") is not None
    assert parse_date("2026-03-15").day == 15

def test_parse_date_slash():
    assert parse_date("2026/03/15") is not None

def test_parse_date_empty():
    assert parse_date("") is None
    assert parse_date("  ") is None

def test_parse_date_invalid():
    assert parse_date("not-a-date") is None

def test_parse_int():
    assert parse_int("42") == 42
    assert parse_int("") is None
    assert parse_int(None) is None
    assert parse_int("abc") is None

def test_parse_float():
    assert parse_float("3.14") == 3.14
    assert parse_float("") is None
    assert parse_float(None) is None

def test_month_key():
    dt = datetime(2026, 3, 15)
    assert month_key(dt) == "2026-03"
    assert month_key(datetime(2026, 12, 1)) == "2026-12"

def test_in_month_range():
    assert in_month_range("2026-03", "2026-01", "2026-06")
    assert not in_month_range("2026-07", "2026-01", "2026-06")

def test_iter_dates():
    dates = list(iter_dates(date(2026, 3, 1), date(2026, 3, 3)))
    assert len(dates) == 3


def test_in_date_range_inclusive():
    start, end = date(2026, 3, 1), date(2026, 3, 31)
    assert in_date_range(date(2026, 3, 15), start, end)
    assert in_date_range(start, start, end)   # inclusive lower bound
    assert in_date_range(end, start, end)     # inclusive upper bound
    assert not in_date_range(date(2026, 2, 28), start, end)
    assert not in_date_range(date(2026, 4, 1), start, end)


def test_parse_datetime_normalizes_to_local_tz_aware():
    # str inputs (UTC, naive) and datetime inputs all come back tz-aware local.
    assert parse_datetime("2026-03-15T10:00:00Z").tzinfo is not None
    assert parse_datetime("2026-03-15T10:00:00").tzinfo is not None
    assert parse_datetime(datetime(2026, 3, 15, 10, 0)).tzinfo is not None


def test_parse_datetime_results_are_comparable():
    # The bug this guards: a naive and an aware parse must not raise TypeError
    # when compared. Both are normalized to local-tz-aware.
    a = parse_datetime("2026-03-15T10:00:00Z")        # was tz-aware
    b = parse_datetime(datetime(2026, 3, 15, 11, 0))  # was naive
    assert (a < b) or (a >= b)  # comparison does not raise
