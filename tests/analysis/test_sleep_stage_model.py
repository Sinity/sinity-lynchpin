"""Tests for the minute-level sleep/wake sensor model."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from lynchpin.analysis import sleep_stage_model as ssm


class _Stage:
    def __init__(self, start: datetime, end: datetime, stage: str) -> None:
        self.start, self.end, self.stage = start, end, stage


def _series(base: datetime, minutes: int, value_fn):
    return {base + timedelta(minutes=i): value_fn(i) for i in range(minutes)}


def _patch_sources(monkeypatch, movement, heart, stages):
    def fake_series(filename, key, start, end):
        return movement if "movement" in filename else heart

    monkeypatch.setattr(ssm, "_load_minute_series", fake_series)
    monkeypatch.setattr("lynchpin.sources.sleep.sleep_stages", lambda **kw: stages)


BASE = datetime(2026, 1, 1, 23, 0)
D = date(2026, 1, 1)


def test_features_carry_baseline_relative_hr_and_labels(monkeypatch):
    movement = _series(BASE, 120, lambda i: 0.2 if i < 60 else 5.0)
    heart = _series(BASE, 120, lambda i: 50.0 if i < 60 else 80.0)
    stages = [
        _Stage(BASE, BASE + timedelta(minutes=60), "deep"),
        _Stage(BASE + timedelta(minutes=60), BASE + timedelta(minutes=120), "awake"),
    ]
    _patch_sources(monkeypatch, movement, heart, stages)

    feats = ssm.build_minute_features(start=D, end=D)
    assert len(feats) == 120
    asleep = [f for f in feats if f.label == 1]
    awake = [f for f in feats if f.label == 0]
    assert len(asleep) == 60 and len(awake) == 60
    # baseline is the low percentile => sleeping minutes sit near ratio 1
    assert abs(asleep[0].hr_ratio - 1.0) < 0.05
    assert awake[0].hr_ratio > 1.4
    assert awake[0].hr_delta > 25
    # circadian encoding is on the unit circle
    for f in feats[:5]:
        assert abs(f.tod_sin**2 + f.tod_cos**2 - 1.0) < 1e-9
    assert feats[0].night == D


def test_unlabelled_minutes_are_retained_unless_filtered(monkeypatch):
    movement = _series(BASE, 60, lambda i: 1.0)
    heart = _series(BASE, 60, lambda i: 60.0)
    stages = [_Stage(BASE, BASE + timedelta(minutes=30), "light")]
    _patch_sources(monkeypatch, movement, heart, stages)

    everything = ssm.build_minute_features(start=D, end=D)
    labelled = ssm.build_minute_features(start=D, end=D, labelled_only=True)
    assert len(everything) == 60
    assert len(labelled) == 30
    assert all(f.label is not None for f in labelled)
    assert any(f.label is None for f in everything)


def test_missing_sensor_yields_no_features(monkeypatch):
    _patch_sources(monkeypatch, {}, {}, [])
    assert ssm.build_minute_features(start=D, end=D) == []


def test_too_little_labelled_data_reports_without_a_model(monkeypatch):
    movement = _series(BASE, 60, lambda i: 1.0)
    heart = _series(BASE, 60, lambda i: 60.0)
    stages = [_Stage(BASE, BASE + timedelta(minutes=60), "light")]
    _patch_sources(monkeypatch, movement, heart, stages)

    model, report = ssm.train_sleep_wake_model(start=D, end=D)
    assert model is None
    assert math.isnan(report.roc_auc)
    assert any("split BY NIGHT" in c for c in report.caveats)


def test_learns_a_separable_signal_and_splits_by_night(monkeypatch):
    """Movement cleanly separates the classes across many nights."""
    movement: dict[datetime, float] = {}
    heart: dict[datetime, float] = {}
    stages = []
    for night in range(40):
        start = BASE + timedelta(days=night)
        for i in range(240):
            minute = start + timedelta(minutes=i)
            asleep = i < 180
            movement[minute] = 0.1 if asleep else 6.0
            heart[minute] = 52.0 if asleep else 85.0
        stages.append(_Stage(start, start + timedelta(minutes=180), "deep"))
        stages.append(
            _Stage(start + timedelta(minutes=180), start + timedelta(minutes=240), "awake")
        )
    _patch_sources(monkeypatch, movement, heart, stages)

    model, report = ssm.train_sleep_wake_model(start=D, end=D + timedelta(days=40))
    assert model is not None
    assert report.n_nights == 40
    assert report.n_minutes == 40 * 240
    assert report.roc_auc > 0.95
    assert report.balanced_accuracy > 0.9
    # more movement must push AWAY from asleep
    assert report.coefficients["activity"] < 0
    assert set(report.coefficients) == set(ssm.FEATURE_NAMES)


def test_report_exposes_the_majority_baseline_for_comparison(monkeypatch):
    movement: dict[datetime, float] = {}
    heart: dict[datetime, float] = {}
    stages = []
    for night in range(30):
        start = BASE + timedelta(days=night)
        for i in range(200):
            minute = start + timedelta(minutes=i)
            asleep = i < 180  # heavily imbalanced
            movement[minute] = 0.5 if asleep else 4.0
            heart[minute] = 55.0 if asleep else 75.0
        stages.append(_Stage(start, start + timedelta(minutes=180), "light"))
        stages.append(
            _Stage(start + timedelta(minutes=180), start + timedelta(minutes=200), "awake")
        )
    _patch_sources(monkeypatch, movement, heart, stages)

    _, report = ssm.train_sleep_wake_model(start=D, end=D + timedelta(days=30))
    assert 0.5 < report.baseline_majority < 1.0
    assert report.n_asleep > report.n_awake
