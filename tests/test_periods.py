from __future__ import annotations

from datetime import date
from pathlib import Path

from lynchpin.core.periods import (
    child_keys,
    child_scale,
    hierarchical_relpath,
    key_for_date,
    next_key,
    parse_period,
    period_keys_in_range,
    prior_key,
)


def test_parse_period_supports_half_and_year() -> None:
    half = parse_period("half", "2026-H1")
    assert half is not None
    assert half.start == date(2026, 1, 1)
    assert half.end == date(2026, 6, 30)

    year = parse_period("year", "2026")
    assert year is not None
    assert year.start == date(2026, 1, 1)
    assert year.end == date(2026, 12, 31)


def test_child_hierarchy_extends_to_half_and_year() -> None:
    assert child_scale("year") == "half"
    assert child_scale("half") == "quarter"
    assert child_keys("year", "2026") == ["2026-H1", "2026-H2"]
    assert child_keys("half", "2026-H1") == ["2026-Q1", "2026-Q2"]


def test_prior_and_next_handle_half_and_year_boundaries() -> None:
    assert prior_key("half", "2026-H1") == "2025-H2"
    assert next_key("half", "2026-H2") == "2027-H1"
    assert prior_key("year", "2026") == "2025"
    assert next_key("year", "2026") == "2027"


def test_hierarchical_relpaths_cover_supported_periods() -> None:
    assert hierarchical_relpath("day", "2026-03-01") == Path("2026/H1/Q1/March/1st.md")
    assert hierarchical_relpath("half", "2026-H2") == Path("2026/H2/2026-H2.md")
    assert hierarchical_relpath("year", "2026") == Path("2026/2026.md")


def test_key_for_date_and_range_cover_supported_scales() -> None:
    value = date(2026, 3, 16)
    assert key_for_date("day", value) == "2026-03-16"
    assert key_for_date("week", value).startswith("2026-W")
    assert key_for_date("month", value) == "2026-03"
    assert key_for_date("quarter", value) == "2026-Q1"
    assert key_for_date("half", value) == "2026-H1"
    assert key_for_date("year", value) == "2026"

    assert period_keys_in_range("day", date(2026, 3, 16), date(2026, 3, 18)) == [
        "2026-03-16",
        "2026-03-17",
        "2026-03-18",
    ]
    assert period_keys_in_range("week", date(2026, 3, 16), date(2026, 3, 23)) == [
        key_for_date("week", date(2026, 3, 16)),
        key_for_date("week", date(2026, 3, 23)),
    ]
