"""Tests for the operator-physiology dimension of the context pack.

Physiology means must exclude missing days (never count them as zero), return
None when no data exists, and surface an explicit staleness caveat when the
observed signals end before the requested window.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.graph import context_pack as cp


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def _health(d, *, stress=None, hrv=None, resting=None, steps=None):
    return SimpleNamespace(
        date=d, stress_avg=stress, hrv_rmssd_avg=hrv,
        heart_rate_resting=resting, steps=steps,
    )


def _sleep(d, *, minutes, score=None):
    return SimpleNamespace(date=d, total_minutes=minutes, avg_score=score)


def _patch(monkeypatch, health, sleep):
    rows = []
    for item in health:
        for metric, attr in (
            ("stress_avg", "stress_avg"),
            ("hrv_rmssd", "hrv_rmssd_avg"),
            ("resting_heart_rate", "heart_rate_resting"),
            ("steps", "steps"),
        ):
            value = getattr(item, attr, None)
            if value is not None:
                rows.append(
                    SimpleNamespace(
                        source="health",
                        date=item.date,
                        metric=metric,
                        value=float(value),
                        dimensions={},
                    )
                )
    for item in sleep:
        rows.append(
            SimpleNamespace(
                source="sleep",
                date=item.date,
                metric="sleep_minutes",
                value=float(item.total_minutes),
                dimensions={},
            )
        )
        if item.avg_score is not None:
            rows.append(
                SimpleNamespace(
                    source="sleep",
                    date=item.date,
                    metric="sleep_score",
                    value=float(item.avg_score),
                    dimensions={},
                )
            )
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window: None,
    )
    monkeypatch.setattr(
        "lynchpin.sources.personal_signals.iter_personal_daily_signals",
        lambda *, start, end, ensure=True: iter(rows),
    )


def test_means_exclude_missing_days(monkeypatch):
    health = [
        _health(date(2026, 3, 1), stress=40.0, resting=60.0, steps=8000),
        _health(date(2026, 3, 2), stress=None, resting=62.0, steps=None),  # partial
        _health(date(2026, 3, 3), stress=50.0, resting=None, steps=10000),
    ]
    sleep = [
        _sleep(date(2026, 3, 1), minutes=420.0, score=80.0),
        _sleep(date(2026, 3, 2), minutes=480.0, score=None),
    ]
    _patch(monkeypatch, health, sleep)

    p = cp._build_physiology(start=_dt(2026, 3, 1), end=_dt(2026, 3, 3))
    assert p is not None
    # stress: only 2 of 3 days present -> mean(40,50)=45, days=2
    assert p.stress_mean == 45.0 and p.stress_days == 2
    # resting HR: 2 present -> mean(60,62)=61
    assert p.resting_hr_mean == 61.0 and p.resting_hr_days == 2
    # steps: 2 present -> mean(8000,10000)=9000
    assert p.steps_mean == 9000.0 and p.steps_days == 2
    # hrv: none present -> None, 0 (NOT a measured zero)
    assert p.hrv_rmssd_mean is None and p.hrv_days == 0
    # sleep hours: mean(7.0, 8.0)=7.5 over 2 days; score only 1 present
    assert p.sleep_hours_mean == 7.5 and p.sleep_days == 2
    assert p.sleep_score_mean == 80.0


def test_none_when_no_data(monkeypatch):
    _patch(monkeypatch, [], [])
    assert cp._build_physiology(start=_dt(2026, 3, 1), end=_dt(2026, 3, 3)) is None


def test_staleness_caveat_when_signals_end_before_window(monkeypatch):
    health = [_health(date(2026, 3, 1), stress=40.0)]
    sleep = [_sleep(date(2026, 3, 2), minutes=420.0)]
    _patch(monkeypatch, health, sleep)

    p = cp._build_physiology(start=_dt(2026, 3, 1), end=_dt(2026, 3, 31))
    assert p is not None
    assert any(c.source == "physiology" and c.status == "partial" for c in p.caveats)
    # observed last = max(2026-03-02 sleep, 2026-03-01 health) = 2026-03-02
    assert "2026-03-02" in p.caveats[0].message

    rendered = cp._render_physiology(p)
    assert "Sleep:" in rendered and "Stress:" in rendered and "_caveat:_" in rendered


def test_no_caveat_when_window_fully_covered(monkeypatch):
    health = [_health(date(2026, 3, 3), stress=40.0)]
    sleep = [_sleep(date(2026, 3, 3), minutes=420.0)]
    _patch(monkeypatch, health, sleep)
    p = cp._build_physiology(start=_dt(2026, 3, 1), end=_dt(2026, 3, 3))
    assert p is not None and p.caveats == ()
