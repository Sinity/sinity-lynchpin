"""Tests for pure helper functions in trajectory/day.py and coverage.py."""

from __future__ import annotations

from datetime import date, datetime, timezone


from lynchpin.trajectory.coverage import _classify_quality
from lynchpin.trajectory.day import _date_range, _split_span_by_day


# ---------------------------------------------------------------------------
# _classify_quality (coverage.py)
# ---------------------------------------------------------------------------

class TestClassifyQuality:
    def test_zero_returns_empty(self) -> None:
        assert _classify_quality(0) == "empty"

    def test_one_returns_sparse(self) -> None:
        assert _classify_quality(1) == "sparse"

    def test_two_returns_moderate(self) -> None:
        assert _classify_quality(2) == "moderate"

    def test_three_returns_moderate(self) -> None:
        assert _classify_quality(3) == "moderate"

    def test_four_returns_rich(self) -> None:
        assert _classify_quality(4) == "rich"

    def test_large_count_returns_rich(self) -> None:
        assert _classify_quality(100) == "rich"


# ---------------------------------------------------------------------------
# _date_range (day.py)
# ---------------------------------------------------------------------------

class TestDateRange:
    def test_same_start_end_returns_one_date(self) -> None:
        d = date(2026, 3, 17)
        assert _date_range(d, d) == [d]

    def test_two_days_returns_both(self) -> None:
        start, end = date(2026, 3, 17), date(2026, 3, 18)
        result = _date_range(start, end)
        assert result == [start, end]

    def test_end_before_start_returns_empty(self) -> None:
        result = _date_range(date(2026, 3, 18), date(2026, 3, 17))
        assert result == []

    def test_full_week(self) -> None:
        start = date(2026, 3, 16)  # Monday
        end = date(2026, 3, 22)    # Sunday
        result = _date_range(start, end)
        assert len(result) == 7
        assert result[0] == start
        assert result[-1] == end

    def test_month_boundary_crossed(self) -> None:
        result = _date_range(date(2026, 1, 30), date(2026, 2, 1))
        assert len(result) == 3
        assert date(2026, 1, 31) in result
        assert date(2026, 2, 1) in result


# ---------------------------------------------------------------------------
# _split_span_by_day (day.py)
# ---------------------------------------------------------------------------

class TestSplitSpanByDay:
    _UTC = timezone.utc

    def _dt(self, day: int, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 3, day, hour, minute, tzinfo=self._UTC)

    def test_end_before_start_returns_empty(self) -> None:
        result = _split_span_by_day(self._dt(17, 10), self._dt(17, 9))
        assert result == []

    def test_end_equal_start_returns_empty(self) -> None:
        result = _split_span_by_day(self._dt(17, 10), self._dt(17, 10))
        assert result == []

    def test_within_same_day(self) -> None:
        # 10:00 to 12:00 on Mar 17 → 7200 seconds on one day
        result = _split_span_by_day(self._dt(17, 10), self._dt(17, 12))
        assert len(result) == 1
        assert result[0][0] == date(2026, 3, 17)
        assert abs(result[0][1] - 7200.0) < 1.0

    def test_spans_midnight(self) -> None:
        # 23:00 Mar 17 to 01:00 Mar 18 → spans 2 days
        start = datetime(2026, 3, 17, 23, 0, tzinfo=self._UTC)
        end = datetime(2026, 3, 18, 1, 0, tzinfo=self._UTC)
        result = _split_span_by_day(start, end)
        assert len(result) == 2
        days = [seg[0] for seg in result]
        assert date(2026, 3, 17) in days
        assert date(2026, 3, 18) in days

    def test_total_seconds_preserved(self) -> None:
        # Total seconds across all segments should match end - start
        start = datetime(2026, 3, 17, 22, 0, tzinfo=self._UTC)
        end = datetime(2026, 3, 19, 2, 0, tzinfo=self._UTC)
        result = _split_span_by_day(start, end)
        total = sum(s for _, s in result)
        expected = (end - start).total_seconds()
        assert abs(total - expected) < 1.0

    def test_exact_midnight_span(self) -> None:
        # 00:00 to 00:00 next day → exactly one day
        start = datetime(2026, 3, 17, 0, 0, tzinfo=self._UTC)
        end = datetime(2026, 3, 18, 0, 0, tzinfo=self._UTC)
        result = _split_span_by_day(start, end)
        assert len(result) == 1
        assert result[0][1] == 86400.0
