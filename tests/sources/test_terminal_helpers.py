"""Tests for pure helper functions in sources/captures/atuin.py."""

from __future__ import annotations

from datetime import datetime, timezone


from lynchpin.sources.terminal import _from_unit, _to_unit


_UTC = timezone.utc
_EPOCH = datetime(2026, 3, 17, 10, 0, 0, tzinfo=_UTC)


# ---------------------------------------------------------------------------
# _to_unit
# ---------------------------------------------------------------------------

class TestToUnit:
    def test_seconds_unit(self) -> None:
        result = _to_unit(_EPOCH, "s")
        assert result == int(_EPOCH.timestamp())

    def test_milliseconds_unit(self) -> None:
        result = _to_unit(_EPOCH, "ms")
        assert result == int(_EPOCH.timestamp() * 1_000)

    def test_nanoseconds_unit(self) -> None:
        result = _to_unit(_EPOCH, "ns")
        assert result == int(_EPOCH.timestamp() * 1_000_000_000)

    def test_returns_integer(self) -> None:
        assert isinstance(_to_unit(_EPOCH, "s"), int)
        assert isinstance(_to_unit(_EPOCH, "ms"), int)
        assert isinstance(_to_unit(_EPOCH, "ns"), int)

    def test_ns_equals_ms_times_million(self) -> None:
        assert _to_unit(_EPOCH, "ns") == _to_unit(_EPOCH, "ms") * 1_000_000


# ---------------------------------------------------------------------------
# _from_unit
# ---------------------------------------------------------------------------

class TestFromUnit:
    def test_seconds_conversion(self) -> None:
        ts = int(_EPOCH.timestamp())
        result = _from_unit(ts, "s")
        assert abs((result - _EPOCH).total_seconds()) < 1.0

    def test_milliseconds_conversion(self) -> None:
        ts = int(_EPOCH.timestamp() * 1_000)
        result = _from_unit(ts, "ms")
        assert abs((result - _EPOCH).total_seconds()) < 1.0

    def test_nanoseconds_conversion(self) -> None:
        ts = int(_EPOCH.timestamp() * 1_000_000_000)
        result = _from_unit(ts, "ns")
        assert abs((result - _EPOCH).total_seconds()) < 1.0

    def test_result_is_utc(self) -> None:
        result = _from_unit(0, "s")
        assert result.tzinfo == _UTC

    def test_zero_returns_epoch(self) -> None:
        result = _from_unit(0, "s")
        assert result.year == 1970


# ---------------------------------------------------------------------------
# Roundtrip: _to_unit → _from_unit
# ---------------------------------------------------------------------------

class TestRoundtrip:
    def test_seconds_roundtrip(self) -> None:
        value = _to_unit(_EPOCH, "s")
        recovered = _from_unit(value, "s")
        assert abs((recovered - _EPOCH).total_seconds()) < 1.0

    def test_milliseconds_roundtrip(self) -> None:
        value = _to_unit(_EPOCH, "ms")
        recovered = _from_unit(value, "ms")
        assert abs((recovered - _EPOCH).total_seconds()) < 0.001

    def test_nanoseconds_roundtrip(self) -> None:
        value = _to_unit(_EPOCH, "ns")
        recovered = _from_unit(value, "ns")
        # Float precision at ns scale may drift slightly
        assert abs((recovered - _EPOCH).total_seconds()) < 0.001
