"""Tests for pure helper functions in lynchpin/metrics/focus.py."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from lynchpin.metrics.focus import (
    FALSE_ACTIVE_APPS,
    _calculate_false_active_minutes,
    window_label,
)

_UTC = timezone.utc


# ---------------------------------------------------------------------------
# window_label
# ---------------------------------------------------------------------------

class TestWindowLabel:
    def test_app_key_returned(self) -> None:
        assert window_label({"app": "kitty"}) == "kitty"

    def test_application_key_used_as_fallback(self) -> None:
        assert window_label({"application": "Firefox"}) == "Firefox"

    def test_appname_key_used(self) -> None:
        assert window_label({"appname": "nvim"}) == "nvim"

    def test_bundle_key_used(self) -> None:
        assert window_label({"bundle": "org.gnome.Nautilus"}) == "org.gnome.Nautilus"

    def test_app_takes_priority_over_title(self) -> None:
        assert window_label({"app": "kitty", "title": "My Title"}) == "kitty"

    def test_title_fallback_when_no_app_keys(self) -> None:
        assert window_label({"title": "some-window"}) == "some-window"

    def test_empty_dict_returns_unknown(self) -> None:
        assert window_label({}) == "unknown"

    def test_whitespace_only_app_skipped(self) -> None:
        result = window_label({"app": "   ", "title": "real title"})
        assert result == "real title"

    def test_title_truncated_at_80(self) -> None:
        long_title = "x" * 100
        result = window_label({"title": long_title})
        assert len(result) == 80

    def test_whitespace_stripped_from_app(self) -> None:
        assert window_label({"app": "  kitty  "}) == "kitty"


# ---------------------------------------------------------------------------
# _calculate_false_active_minutes
# ---------------------------------------------------------------------------

def _win(app: str, start: datetime, end: datetime) -> SimpleNamespace:
    """Create a duck-typed window event with the given app label."""
    return SimpleNamespace(data={"app": app}, start=start, end=end)


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, 17, hour, minute, tzinfo=_UTC)


class TestCalculateFalseActiveMinutes:
    def test_no_windows_returns_zero(self) -> None:
        intervals = [(_dt(10), _dt(11))]
        result = _calculate_false_active_minutes(intervals, [])
        assert result == 0.0

    def test_no_bad_windows_returns_zero(self) -> None:
        # "kitty" is not in FALSE_ACTIVE_APPS
        intervals = [(_dt(10), _dt(11))]
        windows = [_win("kitty", _dt(10), _dt(11))]
        result = _calculate_false_active_minutes(intervals, windows)
        assert result == 0.0

    def test_bad_window_fully_inside_interval(self) -> None:
        # gcr-prompter is a false-active app
        assert "gcr-prompter" in FALSE_ACTIVE_APPS
        intervals = [(_dt(10), _dt(11))]  # 60 min interval
        windows = [_win("gcr-prompter", _dt(10, 15), _dt(10, 45))]  # 30 min overlap
        result = _calculate_false_active_minutes(intervals, windows)
        assert abs(result - 30.0) < 0.01

    def test_bad_window_fully_covers_interval(self) -> None:
        # Window is larger than the active interval
        intervals = [(_dt(10, 30), _dt(10, 45))]  # 15 min
        windows = [_win("gcr-prompter", _dt(10), _dt(11))]  # 60 min window
        result = _calculate_false_active_minutes(intervals, windows)
        assert abs(result - 15.0) < 0.01

    def test_non_overlapping_returns_zero(self) -> None:
        intervals = [(_dt(10), _dt(11))]
        windows = [_win("gcr-prompter", _dt(12), _dt(13))]
        result = _calculate_false_active_minutes(intervals, windows)
        assert result == 0.0

    def test_multiple_overlapping_intervals_accumulated(self) -> None:
        intervals = [(_dt(10), _dt(11)), (_dt(12), _dt(13))]
        windows = [_win("gcr-prompter", _dt(10, 30), _dt(12, 30))]
        # overlap with first interval: 10:30-11:00 = 30 min
        # overlap with second interval: 12:00-12:30 = 30 min
        result = _calculate_false_active_minutes(intervals, windows)
        assert abs(result - 60.0) < 0.01

    def test_empty_intervals_returns_zero(self) -> None:
        windows = [_win("gcr-prompter", _dt(10), _dt(11))]
        result = _calculate_false_active_minutes([], windows)
        assert result == 0.0

    def test_window_missing_start_skipped(self) -> None:
        intervals = [(_dt(10), _dt(11))]
        w = SimpleNamespace(data={"app": "gcr-prompter"}, start=None, end=_dt(11))
        result = _calculate_false_active_minutes(intervals, [w])
        assert result == 0.0

    def test_window_missing_end_skipped(self) -> None:
        intervals = [(_dt(10), _dt(11))]
        w = SimpleNamespace(data={"app": "gcr-prompter"}, start=_dt(10), end=None)
        result = _calculate_false_active_minutes(intervals, [w])
        assert result == 0.0

    def test_returns_float(self) -> None:
        result = _calculate_false_active_minutes([], [])
        assert isinstance(result, float)
