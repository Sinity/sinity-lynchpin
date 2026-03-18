"""Tests for trajectory module pure ID / classification helpers."""

from __future__ import annotations

from datetime import date

import pytest

from lynchpin.trajectory.anomaly import _anomaly_id
from lynchpin.trajectory.episode import _compose_label, _episode_id
from lynchpin.trajectory.week import _classify_day_pattern, _iso_week_key


# ---------------------------------------------------------------------------
# _anomaly_id (anomaly.py)
# ---------------------------------------------------------------------------

class TestAnomalyId:
    def test_returns_string(self) -> None:
        result = _anomaly_id(date(2026, 3, 17), "rhythm_anomaly")
        assert isinstance(result, str)

    def test_length_is_12(self) -> None:
        result = _anomaly_id(date(2026, 3, 17), "mode_shift")
        assert len(result) == 12

    def test_deterministic(self) -> None:
        d = date(2026, 3, 17)
        assert _anomaly_id(d, "recovery_anomaly") == _anomaly_id(d, "recovery_anomaly")

    def test_different_dates_differ(self) -> None:
        assert _anomaly_id(date(2026, 3, 17), "mode_shift") != _anomaly_id(date(2026, 3, 18), "mode_shift")

    def test_different_kinds_differ(self) -> None:
        d = date(2026, 3, 17)
        assert _anomaly_id(d, "mode_shift") != _anomaly_id(d, "rhythm_anomaly")

    def test_hex_chars_only(self) -> None:
        result = _anomaly_id(date(2026, 1, 1), "mode_shift")
        assert all(c in "0123456789abcdef" for c in result)


# ---------------------------------------------------------------------------
# _episode_id (episode.py)
# ---------------------------------------------------------------------------

class TestEpisodeId:
    def test_returns_string(self) -> None:
        result = _episode_id(date(2026, 3, 1), date(2026, 3, 7), "sinex deep")
        assert isinstance(result, str)

    def test_length_is_16(self) -> None:
        result = _episode_id(date(2026, 3, 1), date(2026, 3, 7), "coding")
        assert len(result) == 16

    def test_deterministic(self) -> None:
        a, b = date(2026, 3, 1), date(2026, 3, 7)
        assert _episode_id(a, b, "label") == _episode_id(a, b, "label")

    def test_different_start_dates_differ(self) -> None:
        end = date(2026, 3, 7)
        assert _episode_id(date(2026, 3, 1), end, "x") != _episode_id(date(2026, 3, 2), end, "x")

    def test_different_labels_differ(self) -> None:
        a, b = date(2026, 3, 1), date(2026, 3, 7)
        assert _episode_id(a, b, "sinex") != _episode_id(a, b, "coding")

    def test_hex_chars_only(self) -> None:
        result = _episode_id(date(2026, 1, 1), date(2026, 1, 14), "test")
        assert all(c in "0123456789abcdef" for c in result)


# ---------------------------------------------------------------------------
# _compose_label (episode.py)
# ---------------------------------------------------------------------------

class TestComposeLabel:
    def test_all_components_joined(self) -> None:
        result = _compose_label("coding", "sinex", "rust")
        assert "sinex" in result
        assert "rust" in result
        assert "coding" in result

    def test_no_components_returns_mixed_activity(self) -> None:
        assert _compose_label(None, None) == "mixed activity"

    def test_project_only(self) -> None:
        result = _compose_label(None, "sinex")
        assert "sinex" in result

    def test_mode_only(self) -> None:
        result = _compose_label("deep_work", None)
        assert result == "deep_work"

    def test_topic_matching_project_not_duplicated(self) -> None:
        # topic "sinex" == project "sinex".lower() → should be omitted
        result = _compose_label("coding", "sinex", "sinex")
        assert result.count("sinex") == 1

    def test_topic_different_from_project_included(self) -> None:
        result = _compose_label("coding", "sinex", "rust")
        assert "rust" in result
        assert "sinex" in result

    def test_none_topic_excluded(self) -> None:
        result = _compose_label("coding", "sinex", None)
        assert "None" not in result


# ---------------------------------------------------------------------------
# _iso_week_key (week.py)
# ---------------------------------------------------------------------------

class TestIsoWeekKey:
    def test_format_is_yyyy_w_ww(self) -> None:
        result = _iso_week_key(date(2026, 3, 17))
        assert result.startswith("2026-W")
        assert len(result) == 8  # "2026-W11"

    def test_zero_padded_week(self) -> None:
        # First week of year
        result = _iso_week_key(date(2026, 1, 5))
        assert "W" in result
        # Week number is 2 digits
        week_part = result.split("-W")[1]
        assert len(week_part) == 2

    def test_year_boundary(self) -> None:
        # 2024-12-31 is in week 1 of 2025 per ISO
        result = _iso_week_key(date(2024, 12, 30))
        # Should return 2025-W01
        assert result == "2025-W01"

    def test_same_week_same_key(self) -> None:
        # 2026-03-16 (Mon) and 2026-03-22 (Sun) are the same ISO week
        assert _iso_week_key(date(2026, 3, 16)) == _iso_week_key(date(2026, 3, 22))

    def test_different_weeks_differ(self) -> None:
        assert _iso_week_key(date(2026, 3, 9)) != _iso_week_key(date(2026, 3, 16))


# ---------------------------------------------------------------------------
# _classify_day_pattern (week.py)
# ---------------------------------------------------------------------------

class TestClassifyDayPattern:
    """Uses SimpleNamespace to satisfy the .date/.active_seconds duck type."""

    from types import SimpleNamespace

    def _day(self, weekday_offset: int, active_seconds: float) -> object:
        """weekday_offset: 0=Mon … 6=Sun relative to 2026-03-16 (a Monday)."""
        from types import SimpleNamespace
        from datetime import date
        base = date(2026, 3, 16)  # Monday
        from datetime import timedelta
        d = base + timedelta(days=weekday_offset)
        return SimpleNamespace(date=d, active_seconds=active_seconds)

    def test_empty_returns_uniform(self) -> None:
        assert _classify_day_pattern([]) == "uniform"

    def test_all_zero_returns_uniform(self) -> None:
        days = [self._day(i, 0) for i in range(7)]
        assert _classify_day_pattern(days) == "uniform"

    def test_heavy_weekend_detected(self) -> None:
        # Sat + Sun heavy, Mon-Fri light
        days = [
            self._day(0, 1000), self._day(1, 1000),  # Mon, Tue
            self._day(5, 5000), self._day(6, 5000),  # Sat, Sun
        ]
        result = _classify_day_pattern(days)
        assert result == "weekend_heavy"

    def test_front_loaded_detected(self) -> None:
        # Mon-Wed much heavier than Thu-Fri
        days = [
            self._day(0, 5000), self._day(1, 5000), self._day(2, 5000),  # Mon-Wed
            self._day(3, 500), self._day(4, 500),  # Thu-Fri
        ]
        assert _classify_day_pattern(days) == "front_loaded"

    def test_back_loaded_detected(self) -> None:
        days = [
            self._day(0, 500), self._day(1, 500), self._day(2, 500),  # Mon-Wed
            self._day(3, 5000), self._day(4, 5000),  # Thu-Fri
        ]
        assert _classify_day_pattern(days) == "back_loaded"

    def test_balanced_weekdays_uniform(self) -> None:
        days = [self._day(i, 3600) for i in range(5)]  # All weekdays equal
        assert _classify_day_pattern(days) == "uniform"
