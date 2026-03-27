"""Tests for pure helper functions in retrospective/life_summary_utils.py."""

from __future__ import annotations

from datetime import datetime, timezone

from lynchpin.retrospective.life_summary_utils import (
    _month_after,
    _month_start,
    _render_counter,
)

_UTC = timezone.utc


# ---------------------------------------------------------------------------
# _render_counter
# ---------------------------------------------------------------------------

class TestRenderCounter:
    def test_basic_two_items(self) -> None:
        result = _render_counter([["rust", 10], ["python", 5]])
        assert "rust 10" in result
        assert "python 5" in result

    def test_comma_separated(self) -> None:
        result = _render_counter([["a", 1], ["b", 2]])
        assert "," in result

    def test_empty_returns_empty_string(self) -> None:
        assert _render_counter([]) == ""

    def test_limit_respected(self) -> None:
        items = [[f"item{i}", i] for i in range(20)]
        result = _render_counter(items, limit=3)
        # Only first 3 items included
        assert "item0" in result
        assert "item3" not in result

    def test_default_limit_12(self) -> None:
        items = [[f"item{i}", i] for i in range(20)]
        result = _render_counter(items)
        # Default limit is 12
        assert "item12" not in result
        assert "item11" in result

    def test_single_item(self) -> None:
        result = _render_counter([["nix", 42]])
        assert result == "nix 42"

    def test_order_preserved(self) -> None:
        result = _render_counter([["alpha", 3], ["beta", 1], ["gamma", 2]])
        assert result.index("alpha") < result.index("beta") < result.index("gamma")


# ---------------------------------------------------------------------------
# _month_start
# ---------------------------------------------------------------------------

class TestMonthStart:
    def test_march_2026_first_day(self) -> None:
        result = _month_start("2026-03", _UTC)
        assert result == datetime(2026, 3, 1, tzinfo=_UTC)

    def test_january_first_day(self) -> None:
        result = _month_start("2026-01", _UTC)
        assert result == datetime(2026, 1, 1, tzinfo=_UTC)

    def test_december_first_day(self) -> None:
        result = _month_start("2025-12", _UTC)
        assert result == datetime(2025, 12, 1, tzinfo=_UTC)

    def test_timezone_preserved(self) -> None:
        result = _month_start("2026-06", _UTC)
        assert result.tzinfo == _UTC

    def test_day_is_always_one(self) -> None:
        result = _month_start("2026-08", _UTC)
        assert result.day == 1


# ---------------------------------------------------------------------------
# _month_after
# ---------------------------------------------------------------------------

class TestMonthAfter:
    def test_march_to_april(self) -> None:
        result = _month_after("2026-03", _UTC)
        assert result == datetime(2026, 4, 1, tzinfo=_UTC)

    def test_january_to_february(self) -> None:
        result = _month_after("2026-01", _UTC)
        assert result == datetime(2026, 2, 1, tzinfo=_UTC)

    def test_december_to_january_next_year(self) -> None:
        result = _month_after("2025-12", _UTC)
        assert result == datetime(2026, 1, 1, tzinfo=_UTC)

    def test_november_to_december(self) -> None:
        result = _month_after("2026-11", _UTC)
        assert result == datetime(2026, 12, 1, tzinfo=_UTC)

    def test_timezone_preserved(self) -> None:
        result = _month_after("2026-06", _UTC)
        assert result.tzinfo == _UTC

    def test_always_first_of_month(self) -> None:
        result = _month_after("2026-07", _UTC)
        assert result.day == 1
