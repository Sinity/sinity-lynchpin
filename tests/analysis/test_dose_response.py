from __future__ import annotations

from datetime import date, time

import lynchpin.analysis.dose_response as dr


def _flat_day(level: float = 10.0) -> dict[int, float]:
    return {h: level for h in range(24)}


def _bumped_day(dose_hour: int, base: float = 10.0, bump: float = 30.0) -> dict[int, float]:
    day = _flat_day(base)
    for offset in dr.RESPONSE_HOURS:
        day[dose_hour + offset] = base + bump
    return day


def _patch_doses(monkeypatch, events):
    from types import SimpleNamespace

    def fake_entries_in_range(*, start, end):
        for day, hour in events:
            yield SimpleNamespace(
                date=day,
                time=None if hour is None else time(hour, 5),
                substance="test",
                amount_mg=10.0,
                source="t",
                note="",
            )

    import lynchpin.sources.substance as sub

    monkeypatch.setattr(sub, "entries_in_range", fake_entries_in_range)


def test_detects_constructed_uplift(monkeypatch):
    dose_days = [date(2026, 6, d) for d in (2, 4, 6, 8, 10, 12, 14, 16, 18, 20)]
    control_days = [date(2026, 6, d) for d in (3, 5, 7, 9, 11, 13, 15, 17, 19, 21)]
    activity = {d: _bumped_day(12) for d in dose_days}
    activity.update({d: _flat_day() for d in control_days})
    _patch_doses(monkeypatch, [(d, 12) for d in dose_days])

    report = dr.analyze_dose_response(
        start=date(2026, 6, 1), end=date(2026, 6, 30), activity=activity
    )
    assert report.n_events == 10
    assert report.net_uplift_min_per_h is not None
    assert report.net_uplift_min_per_h > 25  # constructed +30 bump, minus flat controls
    assert report.p_value is not None and report.p_value < 0.01
    assert report.curve_dose[3] > report.curve_dose[-1]  # bump visible in curve


def test_flat_data_yields_no_effect(monkeypatch):
    dose_days = [date(2026, 6, d) for d in range(2, 21, 2)]
    control_days = [date(2026, 6, d) for d in range(3, 22, 2)]
    activity = {d: _flat_day() for d in [*dose_days, *control_days]}
    _patch_doses(monkeypatch, [(d, 12) for d in dose_days])

    report = dr.analyze_dose_response(
        start=date(2026, 6, 1), end=date(2026, 6, 30), activity=activity
    )
    assert report.net_uplift_min_per_h == 0.0


def test_untimed_days_excluded_from_events_and_controls(monkeypatch):
    activity = {date(2026, 6, 2): _flat_day(), date(2026, 6, 3): _flat_day()}
    _patch_doses(monkeypatch, [(date(2026, 6, 2), None)])
    report = dr.analyze_dose_response(
        start=date(2026, 6, 1), end=date(2026, 6, 30), activity=activity
    )
    assert report.n_events == 0
    assert report.n_days_excluded_untimed == 1
    assert report.n_control_days == 1  # the untimed dose day is not a control


def test_edge_windows_dropped(monkeypatch):
    activity = {date(2026, 6, 2): _flat_day(), date(2026, 6, 3): _flat_day()}
    _patch_doses(monkeypatch, [(date(2026, 6, 2), 1)])  # baseline would need hour -2
    report = dr.analyze_dose_response(
        start=date(2026, 6, 1), end=date(2026, 6, 30), activity=activity
    )
    assert report.n_events == 0
    assert report.n_events_dropped_edge == 1
