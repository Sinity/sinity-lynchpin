"""Tests for core/parse.py."""

from datetime import date, datetime
from lynchpin.core.parse import parse_date, parse_int, parse_float, month_key, in_month_range, iter_dates


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
