"""Tests for lynchpin.sources.indices.gitstats pure utility functions."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from lynchpin.sources.indices.gitstats import (
    _month_after,
    _parse_date,
    _parse_git_shortstat,
)


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_date_object_returned_directly(self) -> None:
        d = date(2026, 3, 17)
        assert _parse_date(d) is d

    def test_datetime_object_returns_date_part(self) -> None:
        dt = datetime(2026, 3, 17, 10, 30, tzinfo=timezone.utc)
        result = _parse_date(dt)
        assert result == date(2026, 3, 17)

    def test_iso_date_string_parsed(self) -> None:
        assert _parse_date("2026-03-17") == date(2026, 3, 17)

    def test_iso_datetime_string_returns_date(self) -> None:
        result = _parse_date("2026-03-17T10:00:00+00:00")
        assert result == date(2026, 3, 17)

    def test_z_suffix_handled(self) -> None:
        result = _parse_date("2026-03-17T10:00:00Z")
        assert result == date(2026, 3, 17)

    def test_none_returns_none(self) -> None:
        assert _parse_date(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_date("") is None

    def test_whitespace_string_returns_none(self) -> None:
        assert _parse_date("   ") is None

    def test_invalid_string_returns_none(self) -> None:
        assert _parse_date("not-a-date") is None

    def test_integer_returns_none(self) -> None:
        assert _parse_date(12345) is None


# ---------------------------------------------------------------------------
# _month_after
# ---------------------------------------------------------------------------

class TestMonthAfter:
    def test_mid_year_increments_month(self) -> None:
        assert _month_after("2026-03") == "2026-04"

    def test_december_rolls_over_to_january_next_year(self) -> None:
        assert _month_after("2026-12") == "2027-01"

    def test_january_increments_to_february(self) -> None:
        assert _month_after("2026-01") == "2026-02"

    def test_november_increments_to_december(self) -> None:
        assert _month_after("2026-11") == "2026-12"

    def test_year_preserved_for_non_december(self) -> None:
        result = _month_after("2025-06")
        assert result.startswith("2025-")

    def test_output_format_is_yyyy_mm(self) -> None:
        result = _month_after("2026-03")
        assert len(result) == 7
        assert result[4] == "-"


# ---------------------------------------------------------------------------
# _parse_git_shortstat
# ---------------------------------------------------------------------------

class TestParseGitShortstat:
    def test_typical_line_parsed(self) -> None:
        line = " 3 files changed, 50 insertions(+), 10 deletions(-)"
        result = _parse_git_shortstat(line)
        assert result["files_changed"] == 3
        assert result["lines_added"] == 50
        assert result["lines_deleted"] == 10

    def test_single_file(self) -> None:
        line = " 1 file changed, 5 insertions(+)"
        result = _parse_git_shortstat(line)
        assert result["files_changed"] == 1
        assert result["lines_added"] == 5
        assert result["lines_deleted"] == 0

    def test_deletions_only(self) -> None:
        line = " 2 files changed, 8 deletions(-)"
        result = _parse_git_shortstat(line)
        assert result["files_changed"] == 2
        assert result["lines_added"] == 0
        assert result["lines_deleted"] == 8

    def test_empty_line_returns_all_zeros(self) -> None:
        result = _parse_git_shortstat("")
        assert result == {"files_changed": 0, "lines_added": 0, "lines_deleted": 0}

    def test_singular_insertion_matched(self) -> None:
        line = " 1 file changed, 1 insertion(+)"
        result = _parse_git_shortstat(line)
        assert result["lines_added"] == 1

    def test_singular_deletion_matched(self) -> None:
        line = " 1 file changed, 1 deletion(-)"
        result = _parse_git_shortstat(line)
        assert result["lines_deleted"] == 1

    def test_large_numbers_parsed(self) -> None:
        line = " 100 files changed, 2500 insertions(+), 1200 deletions(-)"
        result = _parse_git_shortstat(line)
        assert result["files_changed"] == 100
        assert result["lines_added"] == 2500
        assert result["lines_deleted"] == 1200
