"""Open-Meteo historical archive weather source — no API key required.

Fetches daily weather for a date range from the Open-Meteo Historical Archive
API (https://archive-api.open-meteo.com/). The service is free, requires no
credentials, and returns the full set of daily meteorological fields needed for
lifestyle × productivity correlation.

Default location: Warsaw, Poland (52.23°N, 21.01°E, Europe/Warsaw timezone),
which is the operator's primary location. Both location and timezone are
module-level constants that can be overridden via function args.

Graduated API:
    daily_weather(start, end, *, lat=.., lon=..) -> list[WeatherDay]
        Per-day meteorological summary for the requested range. Results are
        cached to disk (JSON) keyed by (lat, lon, start, end) so repeated
        queries for the same window do not hit the network.

    _http_get(url) -> dict
        Thin urllib wrapper. Mockable in tests: monkeypatch this function to
        avoid live network calls. Returns the parsed JSON body.

Network failures are raised as ``WeatherUnavailableError`` (subclass of
``SourceUnavailableError``) with a human-readable reason so the calling stack
can degrade gracefully.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from ..core.errors import SourceUnavailableError

logger = logging.getLogger(__name__)

# ── Location / API constants ──────────────────────────────────────────────────

DEFAULT_LAT: float = 52.23
DEFAULT_LON: float = 21.01
DEFAULT_TIMEZONE: str = "Europe/Warsaw"

_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

_DAILY_FIELDS = [
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "sunshine_duration",
    "cloud_cover_mean",
    "wind_speed_10m_max",
    "surface_pressure_mean",
]


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WeatherDay:
    """Daily meteorological summary for one calendar date.

    All numeric fields may be ``None`` when the API did not return a value for
    that field on that day (missing != zero).

    Fields
    ------
    date
        Calendar date (local, Europe/Warsaw by default).
    temperature_2m_mean
        Daily mean 2 m air temperature (°C).
    temperature_2m_max
        Daily maximum 2 m air temperature (°C).
    temperature_2m_min
        Daily minimum 2 m air temperature (°C).
    precipitation_sum
        Total daily precipitation (mm). 0.0 means a genuine dry day;
        ``None`` means the API returned no value.
    sunshine_duration
        Total sunshine duration (seconds). Open-Meteo defines sunshine as
        direct-normal irradiance > 120 W/m².
    cloud_cover_mean
        Mean daily cloud cover (%, 0–100).
    wind_speed_10m_max
        Maximum daily wind speed at 10 m height (km/h).
    surface_pressure_mean
        Mean daily surface pressure (hPa).
    """

    date: date
    temperature_2m_mean: Optional[float]
    temperature_2m_max: Optional[float]
    temperature_2m_min: Optional[float]
    precipitation_sum: Optional[float]
    sunshine_duration: Optional[float]
    cloud_cover_mean: Optional[float]
    wind_speed_10m_max: Optional[float]
    surface_pressure_mean: Optional[float]


# ── Network error ─────────────────────────────────────────────────────────────


class WeatherUnavailableError(SourceUnavailableError):
    """Network or API error when fetching weather data.

    Subclass of ``SourceUnavailableError`` so the degradation machinery in
    operator_daily / readiness treats it consistently with other source outages.
    """


# ── HTTP layer (mockable) ─────────────────────────────────────────────────────


def _http_get(url: str) -> dict[str, object]:
    """Fetch *url* via urllib and return the parsed JSON body.

    Raises ``WeatherUnavailableError`` on network or HTTP errors so callers
    don't need to handle urllib internals. Replace this function in tests via
    ``monkeypatch`` to avoid live network calls.
    """
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = json.load(resp)
        return data if isinstance(data, dict) else {}
    except urllib.error.HTTPError as exc:
        raise WeatherUnavailableError(
            "weather",
            reason=f"Open-Meteo HTTP {exc.code} for {url!r}: {exc.reason}",
        ) from exc
    except urllib.error.URLError as exc:
        raise WeatherUnavailableError(
            "weather",
            reason=f"Open-Meteo network error for {url!r}: {exc.reason}",
        ) from exc
    except Exception as exc:
        raise WeatherUnavailableError(
            "weather",
            reason=f"Unexpected error fetching {url!r}: {exc}",
        ) from exc


# ── Cache ─────────────────────────────────────────────────────────────────────


def _cache_key(lat: float, lon: float, start: date, end: date) -> str:
    return f"weather_{lat:.4f}_{lon:.4f}_{start}_{end}.json"


def _cache_dir() -> Path:
    return Path(".lynchpin/cache")


def _load_cache(path: Path) -> list[dict[str, object]] | None:
    """Return cached payload list or ``None`` on miss/corrupt."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    return None


def _save_cache(path: Path, payload: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")


# ── Parsing ───────────────────────────────────────────────────────────────────


def _parse_response(body: dict[str, object]) -> list[WeatherDay]:
    """Convert Open-Meteo archive response dict to a list of ``WeatherDay``."""
    daily = body.get("daily")
    if not isinstance(daily, dict):
        return []

    dates_raw = daily.get("time", [])
    dates: list[date] = []
    for raw in dates_raw if isinstance(dates_raw, list) else []:
        try:
            dates.append(date.fromisoformat(str(raw)))
        except (ValueError, TypeError):
            continue

    def _col(name: str) -> list[Optional[float]]:
        vals = daily.get(name, [])
        out: list[Optional[float]] = []
        for v in vals if isinstance(vals, list) else []:
            if v is None:
                out.append(None)
            else:
                try:
                    out.append(float(v))
                except (TypeError, ValueError):
                    out.append(None)
        return out

    cols = {f: _col(f) for f in _DAILY_FIELDS}
    n = len(dates)
    result: list[WeatherDay] = []
    for i in range(n):
        result.append(
            WeatherDay(
                date=dates[i],
                temperature_2m_mean=cols["temperature_2m_mean"][i] if i < len(cols["temperature_2m_mean"]) else None,
                temperature_2m_max=cols["temperature_2m_max"][i] if i < len(cols["temperature_2m_max"]) else None,
                temperature_2m_min=cols["temperature_2m_min"][i] if i < len(cols["temperature_2m_min"]) else None,
                precipitation_sum=cols["precipitation_sum"][i] if i < len(cols["precipitation_sum"]) else None,
                sunshine_duration=cols["sunshine_duration"][i] if i < len(cols["sunshine_duration"]) else None,
                cloud_cover_mean=cols["cloud_cover_mean"][i] if i < len(cols["cloud_cover_mean"]) else None,
                wind_speed_10m_max=cols["wind_speed_10m_max"][i] if i < len(cols["wind_speed_10m_max"]) else None,
                surface_pressure_mean=cols["surface_pressure_mean"][i] if i < len(cols["surface_pressure_mean"]) else None,
            )
        )
    return result


def _payload_to_records(days: list[WeatherDay]) -> list[dict[str, object]]:
    """Serialize ``WeatherDay`` list for the on-disk cache."""
    records: list[dict[str, object]] = []
    for d in days:
        records.append(
            {
                "date": d.date.isoformat(),
                "temperature_2m_mean": d.temperature_2m_mean,
                "temperature_2m_max": d.temperature_2m_max,
                "temperature_2m_min": d.temperature_2m_min,
                "precipitation_sum": d.precipitation_sum,
                "sunshine_duration": d.sunshine_duration,
                "cloud_cover_mean": d.cloud_cover_mean,
                "wind_speed_10m_max": d.wind_speed_10m_max,
                "surface_pressure_mean": d.surface_pressure_mean,
            }
        )
    return records


def _records_to_days(records: list[dict[str, object]]) -> list[WeatherDay]:
    """Deserialize on-disk cache records back to ``WeatherDay`` list."""
    days: list[WeatherDay] = []
    for r in records:
        try:
            d = date.fromisoformat(str(r["date"]))
        except (KeyError, ValueError, TypeError):
            continue

        def _f(key: str) -> Optional[float]:
            v = r.get(key)
            if v is None:
                return None
            try:
                return float(v) if isinstance(v, (int, float, str)) else None
            except (TypeError, ValueError):
                return None

        days.append(
            WeatherDay(
                date=d,
                temperature_2m_mean=_f("temperature_2m_mean"),
                temperature_2m_max=_f("temperature_2m_max"),
                temperature_2m_min=_f("temperature_2m_min"),
                precipitation_sum=_f("precipitation_sum"),
                sunshine_duration=_f("sunshine_duration"),
                cloud_cover_mean=_f("cloud_cover_mean"),
                wind_speed_10m_max=_f("wind_speed_10m_max"),
                surface_pressure_mean=_f("surface_pressure_mean"),
            )
        )
    return days


# ── Public API ────────────────────────────────────────────────────────────────


def daily_weather(
    start: date,
    end: date,
    *,
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
    timezone: str = DEFAULT_TIMEZONE,
    cache_dir: Path | None = None,
) -> list[WeatherDay]:
    """Return daily weather for [start, end] (inclusive) at (lat, lon).

    Results are cached to ``.lynchpin/cache/weather_<lat>_<lon>_<start>_<end>.json``
    so repeated calls for the same window are free. The cache is keyed by the
    full (lat, lon, start, end) tuple so overlapping windows do not collide.

    Raises ``WeatherUnavailableError`` on network failure. The error is a
    ``SourceUnavailableError`` subclass and will be caught by the operator-daily
    degradation machinery.

    Args:
        start: First date of the requested window (inclusive).
        end:   Last date of the requested window (inclusive).
        lat:   Latitude (decimal degrees). Default: Warsaw 52.23.
        lon:   Longitude (decimal degrees). Default: Warsaw 21.01.
        timezone: IANA timezone name for the daily aggregates. Default: Europe/Warsaw.
        cache_dir: Override the cache directory. Defaults to ``.lynchpin/cache``.

    Returns:
        List of ``WeatherDay`` objects sorted by date, one per calendar day in
        [start, end]. Days where the API returned no data are absent from the list
        (missing != zero).
    """
    if start > end:
        return []

    cache_path = (cache_dir or _cache_dir()) / _cache_key(lat, lon, start, end)
    cached = _load_cache(cache_path)
    if cached is not None:
        logger.debug("weather: cache hit %s", cache_path)
        return _records_to_days(cached)

    url = (
        f"{_ARCHIVE_URL}?"
        + urllib.parse.urlencode(
            {
                "latitude": lat,
                "longitude": lon,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "daily": ",".join(_DAILY_FIELDS),
                "timezone": timezone,
            }
        )
    )
    logger.debug("weather: fetching %s", url)
    body = _http_get(url)
    days = _parse_response(body)
    _save_cache(cache_path, _payload_to_records(days))
    return days


__all__ = [
    "WeatherDay",
    "WeatherUnavailableError",
    "daily_weather",
    "_http_get",
    "DEFAULT_LAT",
    "DEFAULT_LON",
    "DEFAULT_TIMEZONE",
]
