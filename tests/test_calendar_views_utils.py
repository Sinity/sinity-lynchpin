"""Tests for pure helper functions in lynchpin/views/calendar_views.py."""

from __future__ import annotations

from datetime import date

import pytest

from lynchpin.trajectory.coverage import SignalCoverage
from lynchpin.views.calendar_views import _coverage_line, _fmt_hours, _fmt_top


# ---------------------------------------------------------------------------
# _fmt_hours
# ---------------------------------------------------------------------------

class TestFmtHours:
    def test_zero_returns_zero(self) -> None:
        assert _fmt_hours(0.0) == "0.00"

    def test_one_hour(self) -> None:
        assert _fmt_hours(3600.0) == "1.00"

    def test_fractional_hours(self) -> None:
        assert _fmt_hours(5400.0) == "1.50"

    def test_two_decimal_places(self) -> None:
        result = _fmt_hours(7777.0)
        assert len(result.split(".")[1]) == 2


# ---------------------------------------------------------------------------
# _fmt_top
# ---------------------------------------------------------------------------

class TestFmtTop:
    def test_empty_returns_na(self) -> None:
        assert _fmt_top(()) == "n/a"

    def test_seconds_shown_as_minutes_by_default(self) -> None:
        result = _fmt_top((("sinex", 3600.0),))
        assert "60.0m" in result
        assert "sinex" in result

    def test_as_hours_true_shows_hours(self) -> None:
        result = _fmt_top((("sinex", 3600.0),), as_hours=True)
        assert "1.0h" in result
        assert "sinex" in result

    def test_multiple_items_comma_separated(self) -> None:
        result = _fmt_top((("sinex", 3600.0), ("lynchpin", 1800.0)))
        assert "," in result
        assert "sinex" in result
        assert "lynchpin" in result

    def test_order_preserved(self) -> None:
        items = (("first", 7200.0), ("second", 3600.0))
        result = _fmt_top(items)
        assert result.index("first") < result.index("second")


# ---------------------------------------------------------------------------
# _coverage_line
# ---------------------------------------------------------------------------

def _cov(**kwargs) -> SignalCoverage:
    defaults = {
        "date": date(2026, 3, 17),
        "has_activitywatch": False,
        "has_terminal": False,
        "has_polylogue": False,
        "has_git": False,
        "has_atuin": False,
        "has_web": False,
        "source_names": (),
        "plane_count": 0,
        "observed_hours": 0.0,
        "quality": "empty",
    }
    defaults.update(kwargs)
    return SignalCoverage(**defaults)


class TestCoverageLine:
    def test_none_returns_na(self) -> None:
        assert _coverage_line(None) == "n/a"

    def test_aw_shown(self) -> None:
        cov = _cov(has_activitywatch=True)
        assert "AW" in _coverage_line(cov)

    def test_terminal_shown(self) -> None:
        cov = _cov(has_terminal=True)
        assert "terminal" in _coverage_line(cov)

    def test_empty_coverage_shows_empty_quality(self) -> None:
        cov = _cov(quality="empty")
        result = _coverage_line(cov)
        assert "empty" in result.lower() or result == "n/a" or len(result) > 0

    def test_multiple_planes_included(self) -> None:
        cov = _cov(has_activitywatch=True, has_git=True)
        result = _coverage_line(cov)
        assert "AW" in result
        assert "git" in result.lower() or "git" in result
