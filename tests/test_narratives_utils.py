"""Tests for pure helper functions in retrospective.narrative and session summaries."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from lynchpin.core.dates import iter_dates, parse_iso_dateish
from lynchpin.analysis.knowledge.session_summaries import _estimate_cost


# ---------------------------------------------------------------------------
# core.dates
# ---------------------------------------------------------------------------

class TestDateHelpers:
    def test_same_start_end_returns_one_date(self) -> None:
        d = date(2026, 3, 17)
        assert list(iter_dates(d, d)) == [d]

    def test_two_consecutive_days(self) -> None:
        start, end = date(2026, 3, 17), date(2026, 3, 18)
        result = list(iter_dates(start, end))
        assert result == [start, end]

    def test_end_before_start_returns_empty(self) -> None:
        result = list(iter_dates(date(2026, 3, 18), date(2026, 3, 17)))
        assert result == []

    def test_full_week(self) -> None:
        start = date(2026, 3, 16)  # Monday
        end = date(2026, 3, 22)    # Sunday
        result = list(iter_dates(start, end))
        assert len(result) == 7
        assert result[0] == start
        assert result[-1] == end

    def test_month_boundary_crossed(self) -> None:
        result = list(iter_dates(date(2026, 1, 30), date(2026, 2, 1)))
        assert len(result) == 3
        assert date(2026, 1, 31) in result
        assert date(2026, 2, 1) in result

    def test_dates_are_consecutive(self) -> None:
        start = date(2026, 3, 10)
        end = date(2026, 3, 15)
        result = list(iter_dates(start, end))
        for i in range(1, len(result)):
            assert (result[i] - result[i - 1]) == timedelta(days=1)

    def test_year_boundary(self) -> None:
        result = list(iter_dates(date(2025, 12, 30), date(2026, 1, 2)))
        assert len(result) == 4
        assert date(2026, 1, 1) in result

    def test_parse_iso_dateish_date(self) -> None:
        assert parse_iso_dateish("2026-03-18") == date(2026, 3, 18)

    def test_parse_iso_dateish_datetime(self) -> None:
        assert parse_iso_dateish("2026-03-18T12:34:56Z") == date(2026, 3, 18)

    def test_parse_iso_dateish_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_iso_dateish("   ")


# ---------------------------------------------------------------------------
# _estimate_cost (session_summaries.py)
# ---------------------------------------------------------------------------

class TestEstimateCost:
    def test_known_model_returns_float(self) -> None:
        result = _estimate_cost("gpt-5-mini", 1000, 500)
        assert isinstance(result, float)
        assert result > 0.0

    def test_unknown_model_returns_none(self) -> None:
        assert _estimate_cost("gpt-nonexistent", 1000, 500) is None

    def test_none_prompt_tokens_returns_none(self) -> None:
        assert _estimate_cost("gpt-5-mini", None, 500) is None

    def test_none_completion_tokens_returns_none(self) -> None:
        assert _estimate_cost("gpt-5-mini", 1000, None) is None

    def test_zero_tokens_returns_zero(self) -> None:
        result = _estimate_cost("gpt-5-mini", 0, 0)
        assert result == 0.0

    def test_cost_scales_with_tokens(self) -> None:
        cost_100 = _estimate_cost("gpt-5-mini", 100, 0)
        cost_200 = _estimate_cost("gpt-5-mini", 200, 0)
        assert cost_200 > cost_100

    def test_input_and_output_both_priced(self) -> None:
        input_only = _estimate_cost("gpt-5-mini", 1000, 0)
        output_only = _estimate_cost("gpt-5-mini", 0, 1000)
        both = _estimate_cost("gpt-5-mini", 1000, 1000)
        assert abs(both - (input_only + output_only)) < 1e-10
