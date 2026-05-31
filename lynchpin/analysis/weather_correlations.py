"""Weather × productivity / physiology cross-correlation analysis.

Correlates each daily Open-Meteo weather field against operator-day signals
(deep-work minutes, sleep hours, stress mean) over the intersection of
ActivityWatch / health coverage and the requested window.

STATISTICAL INTEGRITY CONTRACT
------------------------------
Mirrors ``lifestyle_correlations.py`` and ``substance_kinetics.py``:

* **Multiple comparisons.** ``weather_deep_work_correlation`` and
  ``weather_signals_correlation`` evaluate one correlation per (weather-field
  × outcome) pair. All p-values are Benjamini-Hochberg FDR-corrected across the
  full test family in a single pass before any threshold is applied.

* **Missing ≠ zero.** Weather days absent from the Open-Meteo response are not
  coerced to zero. Operator days without ActivityWatch coverage (not in
  ``sources_present``) are not included. Each pair only uses days where BOTH
  the weather value and the operator signal are present.

* **Association, not causation.** All results carry explicit same-day /
  lagged-association caveats inline. Weather correlates with human behaviour
  through many confounders (temperature seasonality, daylight hours, weekend
  patterns); no direction of effect is claimed.

Method: Pearson cross-correlation, FDR-corrected (Benjamini-Hochberg) across
the weather-field family.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from ..core.analytics import _benjamini_hochberg, _pearson_r, _t_test_p
from ..sources.weather import WeatherDay, daily_weather

logger = logging.getLogger(__name__)

#: Minimum paired observations for a correlation to be reported.
MIN_PAIRS: int = 10

#: Benjamini-Hochberg FDR target across the weather-field family.
FDR_TARGET: float = 0.05

#: |r| floor for surfacing exploratory (non-significant) associations.
EXPLORATORY_R: float = 0.20

# Weather field → human-readable label + units
_FIELD_LABELS: dict[str, str] = {
    "temperature_2m_mean": "Mean temp (°C)",
    "temperature_2m_max": "Max temp (°C)",
    "temperature_2m_min": "Min temp (°C)",
    "precipitation_sum": "Precipitation (mm)",
    "sunshine_duration": "Sunshine (s)",
    "cloud_cover_mean": "Cloud cover (%)",
    "wind_speed_10m_max": "Max wind (km/h)",
    "surface_pressure_mean": "Surface pressure (hPa)",
}


def _weather_value(day: WeatherDay, field: str) -> Optional[float]:
    return getattr(day, field, None)


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WeatherCorrelation:
    """One (weather field → operator signal) Pearson correlation.

    ``p_value`` is the raw two-tailed t-test p. ``q_value`` is the
    Benjamini-Hochberg FDR-adjusted p across the full field family in one
    ``weather_signals_correlation`` call. ``significant`` means ``q_value <
    FDR_TARGET``.
    """

    weather_field: str    # e.g. "temperature_2m_mean"
    signal: str           # e.g. "aw_deep_work_min"
    lag_days: int         # 0 = same-day; +1 = weather today → signal tomorrow
    r: float              # Pearson r
    n: int                # paired observations
    p_value: float        # raw two-tailed t-test p
    q_value: float        # BH FDR-adjusted p across the field family
    significant: bool     # q_value < FDR_TARGET
    label: str            # "{field} → {signal} (lag={lag}d)"


@dataclass
class WeatherCorrelationReport:
    """Full weather × operator-signal correlation report.

    The container is mutable so the builder can fill it incrementally; the
    ``WeatherCorrelation`` members it contains are immutable.
    """

    window_start: date
    window_end: date
    n_days_weather: int           # calendar days with weather data
    n_days_aw: int                # calendar days with ActivityWatch coverage
    n_tests: int = 0              # total correlations in the FDR family
    correlations: list[WeatherCorrelation] = field(default_factory=list)
    summary: str = ""
    caveats: list[str] = field(default_factory=list)


# ── Core computation ──────────────────────────────────────────────────────────


def weather_signals_correlation(
    start: date,
    end: date,
    *,
    lat: float = 52.23,
    lon: float = 21.01,
    signals: Optional[list[str]] = None,
    max_lag: int = 1,
) -> WeatherCorrelationReport:
    """Correlate every daily weather field against operator signals over [start, end].

    Each (weather_field × signal × lag) triple that has at least ``MIN_PAIRS``
    valid paired observations is included. p-values are Benjamini-Hochberg
    FDR-corrected across the full test family in one pass.

    Args:
        start, end: Inclusive window. Weather is fetched (or served from cache)
            for this range.
        lat, lon: Location for the weather query. Default: Warsaw.
        signals: Which ``OperatorDay`` fields to correlate against. Defaults to
            ``["aw_deep_work_min", "sleep_hours", "stress_mean"]``.
        max_lag: Maximum lag in days (0 = same day only; 1 = also weather today
            → signal tomorrow). Default 1.

    Returns:
        ``WeatherCorrelationReport`` with FDR-corrected correlations, per-signal
        paired-day counts, a summary string, and caveats.
    """
    from .operator_daily import OperatorDay, operator_daily_matrix

    if signals is None:
        signals = ["aw_deep_work_min", "sleep_hours", "stress_mean"]

    # ── Build signal lookup from operator matrix ──────────────────────────────
    operator_rows = operator_daily_matrix(start, end)

    # Presence / coverage gates per signal (mirrors lifestyle_correlations.py):
    #   aw_deep_work_min → "activitywatch" in sources_present
    #   sleep_hours       → "sleep" in sources_present
    #   stress_mean       → "health" in sources_present
    _presence: dict[str, str] = {
        "aw_deep_work_min": "activitywatch",
        "sleep_hours": "sleep",
        "stress_mean": "health",
        "aw_active_hours": "activitywatch",
        "aw_fragmentation": "activitywatch",
        "git_commits": "git",
    }

    def _signal_val(row: OperatorDay, sig: str) -> Optional[float]:
        val = getattr(row, sig, None)
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def _sig_present(row: OperatorDay, sig: str) -> bool:
        label = _presence.get(sig)
        if label is None:
            return True  # unknown signal: no presence gate
        return label in row.sources_present

    by_date: dict[date, OperatorDay] = {r.date: r for r in operator_rows}

    # ── Fetch weather ─────────────────────────────────────────────────────────
    weather_days = daily_weather(start, end, lat=lat, lon=lon)
    weather_by_date: dict[date, WeatherDay] = {w.date: w for w in weather_days}

    report = WeatherCorrelationReport(
        window_start=start,
        window_end=end,
        n_days_weather=len(weather_days),
        n_days_aw=sum(
            1 for r in operator_rows if "activitywatch" in r.sources_present
        ),
    )

    fields = list(_FIELD_LABELS.keys())
    lags = list(range(0, max_lag + 1))

    # ── Build raw (r, n, p) triples ───────────────────────────────────────────
    raw: list[tuple[str, str, int, float, int, float]] = []  # field, signal, lag, r, n, p

    for field_name in fields:
        for sig in signals:
            for lag in lags:
                xs: list[float] = []
                ys: list[float] = []
                for weather_date, wday in weather_by_date.items():
                    wx = _weather_value(wday, field_name)
                    if wx is None:
                        continue
                    outcome_date = weather_date
                    if lag > 0:
                        from datetime import timedelta
                        outcome_date = weather_date + timedelta(days=lag)
                    op_row = by_date.get(outcome_date)
                    if op_row is None:
                        continue
                    if not _sig_present(op_row, sig):
                        continue
                    wy = _signal_val(op_row, sig)
                    if wy is None:
                        continue
                    xs.append(wx)
                    ys.append(wy)

                if len(xs) < MIN_PAIRS:
                    continue
                r = _pearson_r(xs, ys)
                if r is None or not math.isfinite(r):
                    continue
                n = len(xs)
                if abs(r) >= 1.0:
                    p = 0.0
                else:
                    t_stat = r * math.sqrt((n - 2) / (1.0 - r * r))
                    p = _t_test_p(t_stat, n - 2)
                raw.append((field_name, sig, lag, r, n, p))

    report.n_tests = len(raw)

    if raw:
        pval_map = {i: entry[5] for i, entry in enumerate(raw)}
        q_map = _benjamini_hochberg(pval_map)
        for i, (field_name, sig, lag, r, n, p) in enumerate(raw):
            q = q_map[i]
            report.correlations.append(
                WeatherCorrelation(
                    weather_field=field_name,
                    signal=sig,
                    lag_days=lag,
                    r=round(r, 4),
                    n=n,
                    p_value=round(p, 4),
                    q_value=round(q, 4),
                    significant=q < FDR_TARGET,
                    label=f"{field_name} → {sig} (lag={lag}d)",
                )
            )

    report.correlations.sort(key=lambda c: -abs(c.r))

    report.caveats = [
        "Same-day and lag-1 ASSOCIATIONS — not causation.",
        "Weather correlates with human behaviour through many confounders "
        "(seasonal patterns, daylight length, weekend effects). A surviving "
        "correlation does not establish that weather drives focus or sleep.",
        "Only days where both the weather value AND the operator signal are "
        "present contribute (missing != zero).",
        f"p-values FDR-corrected (Benjamini-Hochberg) across all "
        f"{len(fields)} weather fields × {len(signals)} signals × {len(lags)} lag(s) = "
        f"{report.n_tests} tests.",
        "ActivityWatch coverage is required for aw_deep_work_min; "
        "health/sleep exports may be stale — check sources_present.",
    ]
    report.summary = _build_summary(report)
    return report


def weather_deep_work_correlation(
    start: date,
    end: date,
    *,
    lat: float = 52.23,
    lon: float = 21.01,
) -> WeatherCorrelationReport:
    """Same-day + lag-1 correlation of every weather field vs AW deep-work minutes.

    Convenience wrapper around ``weather_signals_correlation`` scoped to the
    headline productivity signal (``aw_deep_work_min``). FDR correction is
    applied across the 8-field weather family (same-day + lag-1 = 16 tests).

    Requires ActivityWatch coverage for ``aw_deep_work_min`` to be present in
    ``OperatorDay.sources_present``; absent days are excluded (missing != zero).

    Returns a ``WeatherCorrelationReport`` with all 8 weather fields correlated
    against deep-work minutes, ranked by |r|.
    """
    return weather_signals_correlation(
        start,
        end,
        lat=lat,
        lon=lon,
        signals=["aw_deep_work_min"],
        max_lag=1,
    )


# ── Summary ───────────────────────────────────────────────────────────────────


def _build_summary(report: WeatherCorrelationReport) -> str:
    lines = [
        f"Weather × Operator Correlation: {report.window_start} → {report.window_end}",
        f"  Weather days: {report.n_days_weather}  |  "
        f"AW-covered days: {report.n_days_aw}  |  "
        f"Tests in FDR family: {report.n_tests}",
        "",
    ]

    significant = [c for c in report.correlations if c.significant]
    significant.sort(key=lambda c: -abs(c.r))
    if significant:
        lines.append(
            f"FDR-significant associations "
            f"(Benjamini-Hochberg q<{FDR_TARGET:g}):"
        )
        for c in significant:
            direction = "↑" if c.r > 0 else "↓"
            label = _FIELD_LABELS.get(c.weather_field, c.weather_field)
            lines.append(
                f"  r={c.r:+.3f} {direction}  {label} → {c.signal} "
                f"(lag={c.lag_days}d, n={c.n}, p={c.p_value:.4f}, q={c.q_value:.4f})"
            )
    else:
        lines.append(
            f"No associations survive Benjamini-Hochberg FDR correction "
            f"(q<{FDR_TARGET:g}) across {report.n_tests} tests."
        )

    exploratory = [
        c
        for c in report.correlations
        if not c.significant and abs(c.r) >= EXPLORATORY_R
    ]
    exploratory.sort(key=lambda c: -abs(c.r))
    if exploratory:
        lines.append("")
        lines.append(
            f"Exploratory only (|r|≥{EXPLORATORY_R:g} but NOT FDR-significant "
            "— likely noise, do not report as findings):"
        )
        for c in exploratory[:10]:
            direction = "↑" if c.r > 0 else "↓"
            label = _FIELD_LABELS.get(c.weather_field, c.weather_field)
            lines.append(
                f"  r={c.r:+.3f} {direction}  {label} → {c.signal} "
                f"(lag={c.lag_days}d, n={c.n}, p={c.p_value:.4f}, q={c.q_value:.4f})"
            )

    lines.append("")
    lines.append(
        "CAVEAT: these are ASSOCIATIONS, not causation. Weather correlates "
        "with human behaviour through many confounders including seasonal "
        "patterns, daylight length, and weekend effects. Absent days are "
        "excluded, not counted as zero. Interpret only within the per-"
        "correlation n reported above."
    )
    return "\n".join(lines)


__all__ = [
    "WeatherCorrelation",
    "WeatherCorrelationReport",
    "weather_deep_work_correlation",
    "weather_signals_correlation",
]
