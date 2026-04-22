"""Tests for lynchpin.analysis.comparison and canonical pure helper functions."""

from __future__ import annotations

from datetime import datetime


from lynchpin.analysis.comparison import _parse_date, _rolling_best


# ---------------------------------------------------------------------------
# _parse_date (comparison)
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_iso_with_offset_parsed(self) -> None:
        result = _parse_date("2026-03-17T10:00:00+00:00")
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 17

    def test_z_suffix_replaced(self) -> None:
        result = _parse_date("2026-03-17T10:00:00Z")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_preserves_time_components(self) -> None:
        result = _parse_date("2026-03-17T15:30:45+00:00")
        assert result.hour == 15
        assert result.minute == 30
        assert result.second == 45


# ---------------------------------------------------------------------------
# _rolling_best (comparison)
# ---------------------------------------------------------------------------

class TestRollingBest:
    def test_empty_map_returns_zeros(self) -> None:
        result = _rolling_best({}, 7)
        assert result["best_total"] == 0
        assert result["start"] is None
        assert result["end"] is None

    def test_single_day_returns_that_day(self) -> None:
        result = _rolling_best({"2026-03-17": 100}, 7)
        assert result["best_total"] == 100
        assert result["start"] == "2026-03-17"
        assert result["end"] == "2026-03-17"

    def test_two_days_within_window_summed(self) -> None:
        # Both days within 7-day window → sum = 150
        result = _rolling_best({"2026-03-17": 100, "2026-03-18": 50}, 7)
        assert result["best_total"] == 150

    def test_days_outside_window_not_combined(self) -> None:
        # 30 days apart, window_days=7 → best single day = 100
        result = _rolling_best({"2026-03-01": 100, "2026-04-01": 80}, 7)
        assert result["best_total"] == 100

    def test_returns_integer_best_total(self) -> None:
        result = _rolling_best({"2026-03-17": 50}, 7)
        assert isinstance(result["best_total"], int)

    def test_best_window_selected_correctly(self) -> None:
        # Three days: quiet, burst, quiet → window around burst wins
        daily = {
            "2026-03-01": 10,
            "2026-03-10": 200,
            "2026-03-11": 150,
            "2026-03-20": 5,
        }
        result = _rolling_best(daily, 7)
        assert result["best_total"] == 350  # 200 + 150 within 7 days

    def test_all_zeros_returns_zero(self) -> None:
        daily = {"2026-03-01": 0, "2026-03-02": 0}
        result = _rolling_best(daily, 7)
        assert result["best_total"] == 0

    def test_window_of_one_returns_max_single_day(self) -> None:
        # window_days=1 means each day is isolated
        daily = {"2026-03-01": 50, "2026-03-02": 80, "2026-03-03": 30}
        result = _rolling_best(daily, 1)
        assert result["best_total"] == 80
