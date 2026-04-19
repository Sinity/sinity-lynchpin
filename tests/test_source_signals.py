"""Tests for core/signals.py — timezone helpers."""

from datetime import datetime, timezone
from lynchpin.core.parse import as_local, local_tz

UTC = timezone.utc


class TestAsLocal:
    def test_utc_to_local(self):
        dt = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
        local = as_local(dt)
        assert local.tzinfo is not None
        # Same instant, different representation
        assert abs((local - dt).total_seconds()) < 1

    def test_naive_gets_tz(self):
        dt = datetime(2026, 3, 15, 10, 0)
        local = as_local(dt)
        assert local.tzinfo is not None

    def test_already_local(self):
        dt = datetime(2026, 3, 15, 10, 0, tzinfo=local_tz())
        result = as_local(dt)
        assert result == dt
