"""Tests for sources/sleep.py."""

from datetime import date, datetime
from lynchpin.sources.sleep import SleepEntry, SleepSegment, _parse_dt, _parse_date, _safe_float


class TestSleepEntry:
    def test_quality_labels(self):
        good = SleepEntry(date=date(2026, 3, 15), total_minutes=480, segments=(), avg_score=85)
        assert good.quality_label == "good"

        fair = SleepEntry(date=date(2026, 3, 15), total_minutes=360, segments=(), avg_score=65)
        assert fair.quality_label == "fair"

        poor = SleepEntry(date=date(2026, 3, 15), total_minutes=240, segments=(), avg_score=40)
        assert poor.quality_label == "poor"

        unknown = SleepEntry(date=date(2026, 3, 15), total_minutes=0, segments=(), avg_score=None)
        assert unknown.quality_label == "unknown"


class TestHelpers:
    def test_parse_dt(self):
        assert _parse_dt("2026-03-15T10:00:00+01:00") is not None
        assert _parse_dt(None) is None
        assert _parse_dt("") is None

    def test_parse_date(self):
        assert _parse_date("2026-03-15") == date(2026, 3, 15)
        assert _parse_date(None) is None

    def test_safe_float(self):
        assert _safe_float(3.14) == 3.14
        assert _safe_float("55.0") == 55.0
        assert _safe_float(None) is None
