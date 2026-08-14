from datetime import date, timedelta

import lynchpin.analysis.psychometric_correlations as pc


def test_psychometric_correlation_planted_instrument_vs_health_signal(monkeypatch):
    import lynchpin.sources.phone_events as pe
    from lynchpin.analysis.operator_daily import OperatorDay

    base = date(2026, 1, 1)
    days = [base + timedelta(days=i) for i in range(30)]

    # pvt.median_rt_ms rises linearly 250 -> 400ms; hrv falls linearly -> perfect
    # lag-0 negative r.
    instrument_days = [
        pe.InstrumentDay(
            date=d, instrument="pvt", engine="reaction", run_count=1,
            primary_metric_name="median_rt_ms",
            primary_metric_value=round(250 + 150 * i / 29.0, 2),
        )
        for i, d in enumerate(days)
    ]
    monkeypatch.setattr(pe, "daily_instrument_metrics", lambda **kw: instrument_days)
    monkeypatch.setattr(pe, "instrument_runs", lambda **kw: iter(()))
    monkeypatch.setattr(pc, "_wake_regularity_series", lambda start, end: {})

    rows = [
        OperatorDay(date=d, hrv_rmssd=float(100 - 2 * i), sources_present=frozenset({"health"}))
        for i, d in enumerate(days)
    ]
    monkeypatch.setattr(
        "lynchpin.analysis.operator_daily.operator_daily_matrix", lambda start, end: rows
    )

    rep = pc.psychometric_correlation(base, base + timedelta(days=29), min_pairs=10)
    assert rep.instrument_days_in_window == {"pvt": 30}
    hrv = [c for c in rep.correlations if c.predictor == "pvt.primary_metric" and c.outcome == "hrv_rmssd"]
    assert hrv and any(abs(c.r) > 0.9 and c.significant for c in hrv)


def test_psychometric_correlation_sleep_vs_pvt_family(monkeypatch):
    import lynchpin.sources.phone_events as pe
    from lynchpin.analysis.operator_daily import OperatorDay

    base = date(2026, 2, 1)
    days = [base + timedelta(days=i) for i in range(30)]

    # PVT median_rt_ms rises as sleep_hours falls -> negative r.
    instrument_days = [
        pe.InstrumentDay(
            date=d, instrument="pvt", engine="reaction", run_count=1,
            primary_metric_name="median_rt_ms",
            primary_metric_value=round(500 - 10 * i, 2),
        )
        for i, d in enumerate(days)
    ]
    monkeypatch.setattr(pe, "daily_instrument_metrics", lambda **kw: instrument_days)
    monkeypatch.setattr(pe, "instrument_runs", lambda **kw: iter(()))
    monkeypatch.setattr(pc, "_wake_regularity_series", lambda start, end: {})

    rows = [
        OperatorDay(date=d, sleep_hours=round(5.0 + i / 15.0, 2), sources_present=frozenset({"sleep"}))
        for i, d in enumerate(days)
    ]
    monkeypatch.setattr(
        "lynchpin.analysis.operator_daily.operator_daily_matrix", lambda start, end: rows
    )

    rep = pc.psychometric_correlation(base, base + timedelta(days=29), min_pairs=10)
    sleep_vs_rt = [
        c for c in rep.correlations
        if c.predictor == "sleep_hours" and c.outcome == "pvt.median_rt_ms"
    ]
    assert sleep_vs_rt and any(c.r < -0.9 and c.significant for c in sleep_vs_rt)


def test_psychometric_correlation_missing_instrument_day_not_synthesized(monkeypatch):
    import lynchpin.sources.phone_events as pe

    monkeypatch.setattr(pe, "daily_instrument_metrics", lambda **kw: [])
    monkeypatch.setattr(pe, "instrument_runs", lambda **kw: iter(()))
    monkeypatch.setattr(pc, "_wake_regularity_series", lambda start, end: {})
    monkeypatch.setattr("lynchpin.analysis.operator_daily.operator_daily_matrix", lambda start, end: [])

    rep = pc.psychometric_correlation(date(2026, 3, 1), date(2026, 3, 10))
    assert rep.instrument_days_in_window == {}
    assert rep.correlations == []
    assert rep.n_tests == 0


def test_caveats_never_write_a_preregistered_prediction(monkeypatch):
    import lynchpin.sources.phone_events as pe

    monkeypatch.setattr(pe, "daily_instrument_metrics", lambda **kw: [])
    monkeypatch.setattr(pe, "instrument_runs", lambda **kw: iter(()))
    monkeypatch.setattr(pc, "_wake_regularity_series", lambda start, end: {})
    monkeypatch.setattr("lynchpin.analysis.operator_daily.operator_daily_matrix", lambda start, end: [])

    rep = pc.psychometric_correlation(date(2026, 3, 1), date(2026, 3, 10))
    caveat_text = " ".join(rep.caveats)
    assert "sleep-window experiment" in caveat_text
    assert "placeholder" in caveat_text.lower()


def test_circular_sd_minutes_low_variance_near_zero():
    # Wake times all within a few minutes of each other -> small SD.
    hours = [7.0, 7.05, 6.95, 7.1, 7.0]
    sd = pc._circular_sd_minutes(hours)
    assert sd is not None
    assert sd < 10.0


def test_wake_regularity_series_excludes_own_night_and_requires_min_nights():
    from unittest.mock import patch

    base = date(2026, 4, 1)
    # 10 consecutive tracked nights at a stable wake hour.
    wake_hours = {base + timedelta(days=i): 7.0 for i in range(10)}
    with patch.object(pc, "_wake_time_hour_series", return_value=wake_hours):
        series = pc._wake_regularity_series(base + timedelta(days=5), base + timedelta(days=9))
    # Every date in range has >= 3 prior tracked nights, so all should be present.
    for i in range(5, 10):
        assert base + timedelta(days=i) in series
