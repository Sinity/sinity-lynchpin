"""Tests for lynchpin.sources.weather.

All tests mock the HTTP layer — no live network calls.
"""

from __future__ import annotations

import json
from datetime import date

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _open_meteo_response(
    start: date = date(2026, 4, 1),
    n_days: int = 3,
    *,
    temperature_mean: float = 12.5,
    precipitation: float = 0.0,
    sunshine: float = 36000.0,
    cloud_cover: float = 40.0,
    wind_max: float = 18.0,
    pressure: float = 1013.0,
) -> dict:
    """Build a minimal Open-Meteo archive response with n_days of data."""
    from datetime import timedelta

    days = [start + timedelta(days=i) for i in range(n_days)]
    return {
        "latitude": 52.23,
        "longitude": 21.01,
        "timezone": "Europe/Warsaw",
        "daily": {
            "time": [d.isoformat() for d in days],
            "temperature_2m_mean": [temperature_mean] * n_days,
            "temperature_2m_max": [temperature_mean + 5.0] * n_days,
            "temperature_2m_min": [temperature_mean - 5.0] * n_days,
            "precipitation_sum": [precipitation] * n_days,
            "sunshine_duration": [sunshine] * n_days,
            "cloud_cover_mean": [cloud_cover] * n_days,
            "wind_speed_10m_max": [wind_max] * n_days,
            "surface_pressure_mean": [pressure] * n_days,
        },
    }


# ── Parsing tests ──────────────────────────────────────────────────────────────


def test_parse_response_returns_weather_days():
    """_parse_response should convert a well-formed API dict to WeatherDay objects."""
    from lynchpin.sources.weather import _parse_response

    body = _open_meteo_response(date(2026, 4, 1), n_days=3, temperature_mean=10.0)
    days = _parse_response(body)
    assert len(days) == 3
    assert days[0].date == date(2026, 4, 1)
    assert days[1].date == date(2026, 4, 2)
    assert days[2].date == date(2026, 4, 3)


def test_parse_response_field_values():
    """WeatherDay fields should reflect the API values exactly."""
    from lynchpin.sources.weather import _parse_response

    body = _open_meteo_response(
        date(2026, 4, 1),
        n_days=1,
        temperature_mean=8.3,
        precipitation=2.4,
        sunshine=21600.0,
        cloud_cover=75.0,
        wind_max=32.0,
        pressure=1005.5,
    )
    days = _parse_response(body)
    assert len(days) == 1
    d = days[0]
    assert d.temperature_2m_mean == pytest.approx(8.3)
    assert d.temperature_2m_max == pytest.approx(8.3 + 5.0)
    assert d.temperature_2m_min == pytest.approx(8.3 - 5.0)
    assert d.precipitation_sum == pytest.approx(2.4)
    assert d.sunshine_duration == pytest.approx(21600.0)
    assert d.cloud_cover_mean == pytest.approx(75.0)
    assert d.wind_speed_10m_max == pytest.approx(32.0)
    assert d.surface_pressure_mean == pytest.approx(1005.5)


def test_parse_response_handles_none_values():
    """None values in the API response stay None (missing != zero)."""
    from lynchpin.sources.weather import _parse_response

    body = {
        "daily": {
            "time": ["2026-04-01"],
            "temperature_2m_mean": [None],
            "temperature_2m_max": [None],
            "temperature_2m_min": [None],
            "precipitation_sum": [None],
            "sunshine_duration": [None],
            "cloud_cover_mean": [None],
            "wind_speed_10m_max": [None],
            "surface_pressure_mean": [None],
        }
    }
    days = _parse_response(body)
    assert len(days) == 1
    d = days[0]
    assert d.temperature_2m_mean is None
    assert d.precipitation_sum is None
    assert d.sunshine_duration is None


def test_parse_response_empty_body():
    """_parse_response should return an empty list for a missing daily key."""
    from lynchpin.sources.weather import _parse_response

    assert _parse_response({}) == []
    assert _parse_response({"daily": None}) == []


def test_weather_day_is_frozen():
    """WeatherDay must be a frozen dataclass (immutable)."""
    from lynchpin.sources.weather import WeatherDay

    d = WeatherDay(
        date=date(2026, 4, 1),
        temperature_2m_mean=10.0,
        temperature_2m_max=15.0,
        temperature_2m_min=5.0,
        precipitation_sum=0.0,
        sunshine_duration=36000.0,
        cloud_cover_mean=20.0,
        wind_speed_10m_max=10.0,
        surface_pressure_mean=1013.0,
    )
    with pytest.raises((AttributeError, TypeError)):
        d.temperature_2m_mean = 99.0  # type: ignore[misc]


# ── Cache tests ────────────────────────────────────────────────────────────────


def test_cache_round_trip(tmp_path, monkeypatch):
    """daily_weather should write and then read the on-disk cache correctly."""
    import lynchpin.sources.weather as wmod

    calls: list[str] = []

    def mock_http_get(url: str) -> dict:
        calls.append(url)
        return _open_meteo_response(date(2026, 4, 1), n_days=7, temperature_mean=9.0)

    monkeypatch.setattr(wmod, "_http_get", mock_http_get)

    start, end = date(2026, 4, 1), date(2026, 4, 7)

    # First call: hits network
    days1 = wmod.daily_weather(start, end, cache_dir=tmp_path)
    assert len(calls) == 1
    assert len(days1) == 7
    assert days1[0].temperature_2m_mean == pytest.approx(9.0)

    # Second call: served from cache, no new HTTP request
    days2 = wmod.daily_weather(start, end, cache_dir=tmp_path)
    assert len(calls) == 1  # still 1 — no second HTTP call
    assert len(days2) == 7
    assert days2[0].date == days1[0].date


def test_cache_keyed_by_location(tmp_path, monkeypatch):
    """Different (lat, lon) pairs must use separate cache files."""
    import lynchpin.sources.weather as wmod

    call_urls: list[str] = []

    def mock_http_get(url: str) -> dict:
        call_urls.append(url)
        return _open_meteo_response(date(2026, 4, 1), n_days=3)

    monkeypatch.setattr(wmod, "_http_get", mock_http_get)

    start, end = date(2026, 4, 1), date(2026, 4, 3)
    wmod.daily_weather(start, end, lat=52.23, lon=21.01, cache_dir=tmp_path)
    wmod.daily_weather(start, end, lat=51.10, lon=17.03, cache_dir=tmp_path)
    assert len(call_urls) == 2  # two separate HTTP calls for two locations


def test_cache_file_is_valid_json(tmp_path, monkeypatch):
    """The cache file must be valid JSON with one record per day."""
    import lynchpin.sources.weather as wmod

    monkeypatch.setattr(wmod, "_http_get", lambda _: _open_meteo_response(date(2026, 4, 1), n_days=3))

    start, end = date(2026, 4, 1), date(2026, 4, 3)
    wmod.daily_weather(start, end, cache_dir=tmp_path)

    cache_files = list(tmp_path.glob("weather_*.json"))
    assert len(cache_files) == 1
    data = json.loads(cache_files[0].read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 3
    assert data[0]["date"] == "2026-04-01"


def test_corrupt_cache_falls_through_to_network(tmp_path, monkeypatch):
    """A corrupt cache file should be ignored and a fresh network call made."""
    import lynchpin.sources.weather as wmod

    # Write garbage to the cache location
    key = wmod._cache_key(52.23, 21.01, date(2026, 4, 1), date(2026, 4, 3))
    (tmp_path / key).write_text("not json!", encoding="utf-8")

    calls: list[str] = []

    def mock_http_get(url: str) -> dict:
        calls.append(url)
        return _open_meteo_response(date(2026, 4, 1), n_days=3)

    monkeypatch.setattr(wmod, "_http_get", mock_http_get)
    days = wmod.daily_weather(date(2026, 4, 1), date(2026, 4, 3), cache_dir=tmp_path)
    assert len(calls) == 1
    assert len(days) == 3


# ── Network-error handling ────────────────────────────────────────────────────


def test_network_error_raises_weather_unavailable(tmp_path, monkeypatch):
    """A network failure (URLError from urlopen) should surface as
    WeatherUnavailableError — exercising the real _http_get conversion."""
    import urllib.error
    import urllib.request

    from lynchpin.sources.weather import WeatherUnavailableError, daily_weather

    def boom(*args, **kwargs):
        raise urllib.error.URLError("simulated timeout")

    monkeypatch.setattr(urllib.request, "urlopen", boom)

    with pytest.raises(WeatherUnavailableError):
        daily_weather(date(2026, 4, 1), date(2026, 4, 3), cache_dir=tmp_path)


def test_weather_unavailable_is_source_unavailable():
    """WeatherUnavailableError must be a SourceUnavailableError subclass."""
    from lynchpin.core.errors import SourceUnavailableError
    from lynchpin.sources.weather import WeatherUnavailableError

    assert issubclass(WeatherUnavailableError, SourceUnavailableError)


# ── Empty range ───────────────────────────────────────────────────────────────


def test_daily_weather_empty_when_start_after_end(tmp_path, monkeypatch):
    """start > end should return an empty list without hitting the network."""
    import lynchpin.sources.weather as wmod

    calls: list[str] = []
    monkeypatch.setattr(wmod, "_http_get", lambda url: calls.append(url) or {})

    result = wmod.daily_weather(date(2026, 4, 7), date(2026, 4, 1), cache_dir=tmp_path)
    assert result == []
    assert calls == []
