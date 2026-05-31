"""Tests for lynchpin.analysis.weather_correlations.

Mocks both daily_weather (no live HTTP) and operator_daily_matrix (no real
data). Plants known correlations and verifies FDR machinery, result structure,
and missing-not-zero gating.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pytest


# ── Fixtures ───────────────────────────────────────────────────────────────────


def _make_weather_days(
    start: date,
    n: int,
    *,
    temp_values: Optional[list[float]] = None,
    precipitation: float = 0.0,
    sunshine: float = 36000.0,
    cloud_cover: float = 30.0,
    wind_max: float = 15.0,
    pressure: float = 1013.0,
) -> list:
    """Build a list of WeatherDay objects for testing."""
    from lynchpin.sources.weather import WeatherDay

    temps = temp_values if temp_values is not None else [10.0 + i for i in range(n)]
    days = []
    for i in range(n):
        days.append(
            WeatherDay(
                date=start + timedelta(days=i),
                temperature_2m_mean=temps[i],
                temperature_2m_max=temps[i] + 5.0,
                temperature_2m_min=temps[i] - 5.0,
                precipitation_sum=precipitation,
                sunshine_duration=sunshine,
                cloud_cover_mean=cloud_cover,
                wind_speed_10m_max=wind_max,
                surface_pressure_mean=pressure,
            )
        )
    return days


def _make_operator_days(
    start: date,
    n: int,
    *,
    deep_work_values: Optional[list[Optional[float]]] = None,
    sleep_hours_values: Optional[list[Optional[float]]] = None,
    stress_mean_values: Optional[list[Optional[float]]] = None,
    aw_present: bool = True,
    health_present: bool = True,
    sleep_present: bool = True,
) -> list:
    """Build a list of OperatorDay objects for testing."""
    from lynchpin.analysis.operator_daily import OperatorDay

    dw = deep_work_values if deep_work_values is not None else [60.0 + i * 5.0 for i in range(n)]
    sl = sleep_hours_values if sleep_hours_values is not None else [7.5] * n
    st = stress_mean_values if stress_mean_values is not None else [30.0] * n

    present: set[str] = set()
    if aw_present:
        present.add("activitywatch")
    if health_present:
        present.add("health")
    if sleep_present:
        present.add("sleep")

    days = []
    for i in range(n):
        days.append(
            OperatorDay(
                date=start + timedelta(days=i),
                aw_deep_work_min=dw[i],
                sleep_hours=sl[i],
                stress_mean=st[i],
                sources_present=frozenset(present),
            )
        )
    return days


# ── Smoke test: structure of WeatherCorrelationReport ────────────────────────


def test_weather_signals_correlation_returns_report(monkeypatch):
    """weather_signals_correlation should return a WeatherCorrelationReport."""
    import lynchpin.analysis.weather_correlations as wcorr
    import lynchpin.sources.weather as wmod
    from lynchpin.analysis.weather_correlations import WeatherCorrelationReport

    start, end = date(2026, 1, 1), date(2026, 3, 31)
    n = (end - start).days + 1

    monkeypatch.setattr(wmod, "_http_get", lambda _: {})
    monkeypatch.setattr(
        wcorr,
        "daily_weather",
        lambda *a, **kw: _make_weather_days(start, n),
    )

    import lynchpin.analysis.operator_daily as od

    monkeypatch.setattr(
        od,
        "operator_daily_matrix",
        lambda s, e: _make_operator_days(start, n),
    )

    report = wcorr.weather_signals_correlation(start, end)
    assert isinstance(report, WeatherCorrelationReport)
    assert report.window_start == start
    assert report.window_end == end
    assert report.n_days_weather == n
    assert isinstance(report.correlations, list)
    assert isinstance(report.caveats, list)
    assert len(report.caveats) >= 3
    assert report.summary != ""


def test_weather_deep_work_correlation_is_subset(monkeypatch):
    """weather_deep_work_correlation should only include aw_deep_work_min signals."""
    import lynchpin.analysis.weather_correlations as wcorr
    import lynchpin.sources.weather as wmod

    start, end = date(2026, 1, 1), date(2026, 2, 28)
    n = (end - start).days + 1

    monkeypatch.setattr(wmod, "_http_get", lambda _: {})
    monkeypatch.setattr(
        wcorr,
        "daily_weather",
        lambda *a, **kw: _make_weather_days(start, n),
    )

    import lynchpin.analysis.operator_daily as od

    monkeypatch.setattr(
        od,
        "operator_daily_matrix",
        lambda s, e: _make_operator_days(start, n),
    )

    report = wcorr.weather_deep_work_correlation(start, end)
    # All correlations must target aw_deep_work_min
    for c in report.correlations:
        assert c.signal == "aw_deep_work_min"


# ── Planted correlation ───────────────────────────────────────────────────────


def test_planted_temperature_deep_work_correlation(monkeypatch):
    """A planted linear relationship between temperature and deep-work should
    survive with a strong r value and correct directionality.

    Temperature rises from 0 to n-1 degrees; deep-work mirrors it linearly.
    The correlation should be near +1.0.
    """
    import lynchpin.analysis.weather_correlations as wcorr
    import lynchpin.sources.weather as wmod
    from lynchpin.sources.weather import WeatherDay

    start, end = date(2025, 1, 1), date(2025, 3, 31)
    n = (end - start).days + 1

    # Perfect positive linear relationship: temp = i, deep_work = 10 + i*3
    temp_vals = [float(i) for i in range(n)]
    deep_vals = [10.0 + i * 3.0 for i in range(n)]

    weather_days = []
    for i in range(n):
        weather_days.append(
            WeatherDay(
                date=start + timedelta(days=i),
                temperature_2m_mean=temp_vals[i],
                temperature_2m_max=temp_vals[i] + 5.0,
                temperature_2m_min=temp_vals[i] - 5.0,
                precipitation_sum=0.0,
                sunshine_duration=36000.0,
                cloud_cover_mean=30.0,
                wind_speed_10m_max=10.0,
                surface_pressure_mean=1013.0,
            )
        )

    monkeypatch.setattr(wmod, "_http_get", lambda _: {})
    monkeypatch.setattr(
        wcorr,
        "daily_weather",
        lambda *a, **kw: weather_days,
    )

    import lynchpin.analysis.operator_daily as od

    monkeypatch.setattr(
        od,
        "operator_daily_matrix",
        lambda s, e: _make_operator_days(start, n, deep_work_values=deep_vals),
    )

    report = wcorr.weather_deep_work_correlation(start, end)

    # Find the same-day temperature_2m_mean → aw_deep_work_min correlation
    same_day_temp = [
        c for c in report.correlations
        if c.weather_field == "temperature_2m_mean"
        and c.signal == "aw_deep_work_min"
        and c.lag_days == 0
    ]
    assert len(same_day_temp) == 1
    c = same_day_temp[0]
    assert c.r == pytest.approx(1.0, abs=1e-3), f"Expected r≈1.0, got {c.r}"
    assert c.n == n
    assert c.p_value == pytest.approx(0.0, abs=1e-6)
    assert c.significant is True  # r=1.0 survives any FDR threshold


def test_planted_negative_correlation(monkeypatch):
    """A planted negative relationship (more cloud cover → less deep-work)
    should produce r ≈ -1.0.
    """
    import lynchpin.analysis.weather_correlations as wcorr
    import lynchpin.sources.weather as wmod
    from lynchpin.sources.weather import WeatherDay

    start, end = date(2025, 1, 1), date(2025, 3, 31)
    n = (end - start).days + 1

    cloud_vals = [float(i % 100) for i in range(n)]   # 0..99 cycling
    deep_vals = [100.0 - cloud_vals[i] for i in range(n)]  # perfect inverse

    weather_days = [
        WeatherDay(
            date=start + timedelta(days=i),
            temperature_2m_mean=15.0,
            temperature_2m_max=20.0,
            temperature_2m_min=10.0,
            precipitation_sum=0.0,
            sunshine_duration=36000.0,
            cloud_cover_mean=cloud_vals[i],
            wind_speed_10m_max=10.0,
            surface_pressure_mean=1013.0,
        )
        for i in range(n)
    ]

    monkeypatch.setattr(wmod, "_http_get", lambda _: {})
    monkeypatch.setattr(wcorr, "daily_weather", lambda *a, **kw: weather_days)

    import lynchpin.analysis.operator_daily as od

    monkeypatch.setattr(
        od,
        "operator_daily_matrix",
        lambda s, e: _make_operator_days(start, n, deep_work_values=deep_vals),
    )

    report = wcorr.weather_signals_correlation(start, end, signals=["aw_deep_work_min"], max_lag=0)
    cloud_corr = [
        c for c in report.correlations
        if c.weather_field == "cloud_cover_mean" and c.signal == "aw_deep_work_min"
    ]
    assert len(cloud_corr) == 1
    assert cloud_corr[0].r < -0.9


# ── Missing-not-zero gating ───────────────────────────────────────────────────


def test_missing_aw_days_excluded(monkeypatch):
    """Days without ActivityWatch coverage must not contribute to the correlation."""
    import lynchpin.analysis.weather_correlations as wcorr
    import lynchpin.sources.weather as wmod
    from lynchpin.analysis.operator_daily import OperatorDay

    start, end = date(2026, 1, 1), date(2026, 3, 31)
    n = (end - start).days + 1

    monkeypatch.setattr(wmod, "_http_get", lambda _: {})
    monkeypatch.setattr(wcorr, "daily_weather", lambda *a, **kw: _make_weather_days(start, n))

    import lynchpin.analysis.operator_daily as od

    # Half the days have AW present, half don't
    def make_partial_aw(s: date, e: date) -> list[OperatorDay]:
        rows = []
        for i in range(n):
            has_aw = i % 2 == 0
            rows.append(
                OperatorDay(
                    date=start + timedelta(days=i),
                    aw_deep_work_min=60.0,
                    sources_present=frozenset({"activitywatch"} if has_aw else {}),
                )
            )
        return rows

    monkeypatch.setattr(od, "operator_daily_matrix", make_partial_aw)

    report = wcorr.weather_deep_work_correlation(start, end)
    for c in report.correlations:
        # n must not exceed the number of AW-present days
        assert c.n <= n // 2 + 1, f"{c.weather_field}: n={c.n} exceeded AW-present count"


def test_no_aw_days_produces_no_correlations(monkeypatch):
    """When no operator days have ActivityWatch coverage, no correlations are emitted."""
    import lynchpin.analysis.weather_correlations as wcorr
    import lynchpin.sources.weather as wmod
    from lynchpin.analysis.operator_daily import OperatorDay

    start, end = date(2026, 1, 1), date(2026, 2, 28)
    n = (end - start).days + 1

    monkeypatch.setattr(wmod, "_http_get", lambda _: {})
    monkeypatch.setattr(wcorr, "daily_weather", lambda *a, **kw: _make_weather_days(start, n))

    import lynchpin.analysis.operator_daily as od

    monkeypatch.setattr(
        od,
        "operator_daily_matrix",
        lambda s, e: [
            OperatorDay(
                date=start + timedelta(days=i),
                aw_deep_work_min=60.0,
                sources_present=frozenset(),  # no AW
            )
            for i in range(n)
        ],
    )

    report = wcorr.weather_deep_work_correlation(start, end)
    assert report.correlations == []
    assert report.n_tests == 0


# ── FDR machinery ─────────────────────────────────────────────────────────────


def test_fdr_fields_populated(monkeypatch):
    """Every WeatherCorrelation must carry p_value, q_value, and significant."""
    import lynchpin.analysis.weather_correlations as wcorr
    import lynchpin.sources.weather as wmod

    start, end = date(2025, 1, 1), date(2025, 3, 31)
    n = (end - start).days + 1

    monkeypatch.setattr(wmod, "_http_get", lambda _: {})
    monkeypatch.setattr(wcorr, "daily_weather", lambda *a, **kw: _make_weather_days(start, n))

    import lynchpin.analysis.operator_daily as od

    monkeypatch.setattr(
        od,
        "operator_daily_matrix",
        lambda s, e: _make_operator_days(start, n),
    )

    report = wcorr.weather_signals_correlation(start, end, signals=["aw_deep_work_min"], max_lag=0)
    for c in report.correlations:
        assert 0.0 <= c.p_value <= 1.0, f"p_value out of range: {c.p_value}"
        assert 0.0 <= c.q_value <= 1.0, f"q_value out of range: {c.q_value}"
        assert isinstance(c.significant, bool)
        # q_value must be >= p_value (BH never decreases p below raw)
        assert c.q_value >= c.p_value - 1e-9


def test_significant_correlation_survives_fdr(monkeypatch):
    """A perfect r=1.0 correlation must survive Benjamini-Hochberg FDR correction."""
    import lynchpin.analysis.weather_correlations as wcorr
    import lynchpin.sources.weather as wmod
    from lynchpin.sources.weather import WeatherDay

    start, end = date(2025, 1, 1), date(2025, 6, 30)
    n = (end - start).days + 1
    vals = [float(i) for i in range(n)]

    weather_days = [
        WeatherDay(
            date=start + timedelta(days=i),
            temperature_2m_mean=vals[i],
            temperature_2m_max=vals[i] + 5,
            temperature_2m_min=vals[i] - 5,
            precipitation_sum=0.0,
            sunshine_duration=36000.0,
            cloud_cover_mean=50.0,
            wind_speed_10m_max=10.0,
            surface_pressure_mean=1013.0,
        )
        for i in range(n)
    ]
    monkeypatch.setattr(wmod, "_http_get", lambda _: {})
    monkeypatch.setattr(wcorr, "daily_weather", lambda *a, **kw: weather_days)

    import lynchpin.analysis.operator_daily as od

    # deep-work perfectly mirrors temperature → r=1.0
    monkeypatch.setattr(
        od,
        "operator_daily_matrix",
        lambda s, e: _make_operator_days(start, n, deep_work_values=vals),
    )

    report = wcorr.weather_deep_work_correlation(start, end)
    perfect = [
        c for c in report.correlations
        if c.weather_field == "temperature_2m_mean" and c.lag_days == 0
    ]
    assert perfect, "Expected temperature_2m_mean correlation in report"
    assert perfect[0].significant is True


# ── WeatherCorrelation immutability ──────────────────────────────────────────


def test_weather_correlation_is_frozen():
    """WeatherCorrelation must be a frozen dataclass."""
    from lynchpin.analysis.weather_correlations import WeatherCorrelation

    c = WeatherCorrelation(
        weather_field="temperature_2m_mean",
        signal="aw_deep_work_min",
        lag_days=0,
        r=0.5,
        n=30,
        p_value=0.01,
        q_value=0.05,
        significant=True,
        label="temperature_2m_mean → aw_deep_work_min (lag=0d)",
    )
    with pytest.raises((AttributeError, TypeError)):
        c.r = 0.99  # type: ignore[misc]


# ── Lag mechanics ─────────────────────────────────────────────────────────────


def test_lag_1_uses_next_day_signal(monkeypatch):
    """lag_days=1 must match weather on day D to signal on day D+1."""
    import lynchpin.analysis.weather_correlations as wcorr
    import lynchpin.sources.weather as wmod
    from lynchpin.analysis.operator_daily import OperatorDay
    from lynchpin.sources.weather import WeatherDay

    # 30 days; sunshine increases daily; deep-work on day D+1 mirrors sunshine on D
    n = 30
    start = date(2026, 1, 1)

    sunshine_vals = [float(i * 1000) for i in range(n)]
    # Next-day deep-work = sunshine of prior day
    deep_vals_by_date = {
        start + timedelta(days=i + 1): sunshine_vals[i]
        for i in range(n - 1)
    }

    weather_days = [
        WeatherDay(
            date=start + timedelta(days=i),
            temperature_2m_mean=15.0,
            temperature_2m_max=20.0,
            temperature_2m_min=10.0,
            precipitation_sum=0.0,
            sunshine_duration=sunshine_vals[i],
            cloud_cover_mean=30.0,
            wind_speed_10m_max=10.0,
            surface_pressure_mean=1013.0,
        )
        for i in range(n)
    ]

    # Operator rows cover all n days; deep_work on day D+1 mirrors sunshine on D
    op_rows = [
        OperatorDay(
            date=start + timedelta(days=i),
            aw_deep_work_min=deep_vals_by_date.get(start + timedelta(days=i)),
            sources_present=frozenset({"activitywatch"}),
        )
        for i in range(n)
    ]

    monkeypatch.setattr(wmod, "_http_get", lambda _: {})
    monkeypatch.setattr(wcorr, "daily_weather", lambda *a, **kw: weather_days)

    import lynchpin.analysis.operator_daily as od

    monkeypatch.setattr(od, "operator_daily_matrix", lambda s, e: op_rows)

    report = wcorr.weather_signals_correlation(
        start, start + timedelta(days=n - 1),
        signals=["aw_deep_work_min"],
        max_lag=1,
    )

    lag1_sunshine = [
        c for c in report.correlations
        if c.weather_field == "sunshine_duration"
        and c.signal == "aw_deep_work_min"
        and c.lag_days == 1
    ]
    assert len(lag1_sunshine) == 1
    assert lag1_sunshine[0].r == pytest.approx(1.0, abs=1e-3)
