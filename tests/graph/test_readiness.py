"""Tests for composite.readiness."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from lynchpin.graph import readiness
from lynchpin.graph.readiness import (
    MIN_R_SQUARED,
    MIN_SAMPLE_N,
    ReadinessForecast,
    ReadinessUnavailable,
    build_readiness_forecast,
    readiness_payload,
)


def _make_sleep_productivity_row(
    on: date, *, sleep_hours: float, sleep_score: float, target_deep_work: float
) -> SimpleNamespace:
    return SimpleNamespace(
        sleep_date=on,
        sleep_hours=sleep_hours,
        sleep_score=sleep_score,
        sleep_quality="good",
        workday_active_hours=5.0,
        workday_deep_work_min=target_deep_work,
        productivity_vs_baseline=1.0,
    )


def _make_health_row(on: date, *, hrv: float, resting_hr: float) -> SimpleNamespace:
    return SimpleNamespace(
        date=on,
        hrv_rmssd_avg=hrv,
        heart_rate_resting=resting_hr,
    )


def _make_aw_row(on: date, *, active_hours: float, deep_work_min: float) -> SimpleNamespace:
    return SimpleNamespace(
        date=on,
        active_hours=active_hours,
        deep_work_min=deep_work_min,
        fragmentation_score=0.3,
        project_count=1,
        dominant_mode="coding",
        dominant_project="lynchpin",
        hourly_active=tuple([0.0] * 24),
    )


def _install_synthetic_history(
    monkeypatch: pytest.MonkeyPatch,
    target_date: date,
    *,
    n_days: int = 60,
    correlation_strength: float = 1.0,
) -> None:
    """Install fakes such that target ≈ 5 * sleep_hours + correlation_strength * (other features).

    With strong correlation_strength the OLS r² should clear the gate.
    """
    sp_rows: list[SimpleNamespace] = []
    health_rows: list[SimpleNamespace] = []
    aw_rows: list[SimpleNamespace] = []
    for i in range(n_days):
        d = target_date - timedelta(days=n_days - i)
        sleep_hours = 6.0 + (i % 5) * 0.5
        sleep_score = 70.0 + (i % 7)
        target = correlation_strength * (sleep_hours * 20 + sleep_score * 0.5)
        sp_rows.append(_make_sleep_productivity_row(
            d, sleep_hours=sleep_hours, sleep_score=sleep_score, target_deep_work=target
        ))
        health_rows.append(_make_health_row(d, hrv=40.0 + (i % 6), resting_hr=58.0 + (i % 4)))
        aw_rows.append(_make_aw_row(d, active_hours=4.0 + (i % 3), deep_work_min=target * 0.5))

    monkeypatch.setattr(
        "lynchpin.sources.sleep.sleep_productivity",
        lambda **kwargs: sp_rows,
    )
    monkeypatch.setattr(
        "lynchpin.sources.health.daily_health_summary",
        lambda **kwargs: health_rows,
    )
    monkeypatch.setattr(
        "lynchpin.sources.activitywatch_derived.iter_derived_daily_activity",
        lambda **kwargs: aw_rows,
    )

    # Forecast inputs (sleep night before target)
    monkeypatch.setattr(
        "lynchpin.sources.sleep.sleep_for_date",
        lambda d: SimpleNamespace(
            avg_score=75.0, total_minutes=420.0, date=d, segments=()
        ),
    )


def test_strong_correlation_yields_forecast(monkeypatch: pytest.MonkeyPatch) -> None:
    target = date(2026, 5, 7)
    _install_synthetic_history(monkeypatch, target, n_days=60, correlation_strength=1.0)

    result = build_readiness_forecast(target_date=target, window_days=60)
    assert isinstance(result, ReadinessForecast)
    assert result.sample_n >= MIN_SAMPLE_N
    assert result.r_squared >= MIN_R_SQUARED
    assert result.predicted_deep_work_min >= 0
    low, high = result.confidence_interval_95
    assert low <= result.predicted_deep_work_min <= high
    # Coefficients exposed for audit
    assert "sleep_hours" in result.coefficients
    assert "sleep_score" in result.coefficients


def test_insufficient_history_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    target = date(2026, 5, 7)
    _install_synthetic_history(monkeypatch, target, n_days=10)

    result = build_readiness_forecast(target_date=target, window_days=60)
    assert isinstance(result, ReadinessUnavailable)
    assert "insufficient history" in result.reason
    assert result.r_squared is None


def test_weak_fit_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    target = date(2026, 5, 7)

    # Random-ish targets that don't depend on features → low r².
    sp_rows: list[SimpleNamespace] = []
    health_rows: list[SimpleNamespace] = []
    aw_rows: list[SimpleNamespace] = []
    for i in range(60):
        d = target - timedelta(days=60 - i)
        sp_rows.append(_make_sleep_productivity_row(
            d, sleep_hours=7.0, sleep_score=80.0,
            target_deep_work=(i * 13 % 100) + 50,
        ))
        health_rows.append(_make_health_row(d, hrv=42.0, resting_hr=60.0))
        aw_rows.append(_make_aw_row(d, active_hours=5.0, deep_work_min=70.0))

    monkeypatch.setattr("lynchpin.sources.sleep.sleep_productivity", lambda **kwargs: sp_rows)
    monkeypatch.setattr("lynchpin.sources.health.daily_health_summary", lambda **kwargs: health_rows)
    monkeypatch.setattr("lynchpin.sources.activitywatch_derived.iter_derived_daily_activity", lambda **kwargs: aw_rows)

    result = build_readiness_forecast(target_date=target, window_days=60)
    assert isinstance(result, ReadinessUnavailable)
    assert "model fit too weak" in result.reason


def test_payload_renders_both_states() -> None:
    target = date(2026, 5, 7)
    forecast = ReadinessForecast(
        target_date=target,
        predicted_deep_work_min=120.0,
        confidence_interval_95=(80.0, 160.0),
        inputs={"sleep_hours": 7.0},
        coefficients={"sleep_hours": 15.0},
        intercept=10.0,
        r_squared=0.4,
        sample_n=50,
        caveats=("test",),
    )
    payload = readiness_payload(forecast)
    assert payload["status"] == "available"
    assert payload["predicted_deep_work_min"] == 120.0

    unavailable = ReadinessUnavailable(target_date=target, reason="x", sample_n=5, r_squared=None)
    payload2 = readiness_payload(unavailable)
    assert payload2["status"] == "unavailable"
    assert payload2["reason"] == "x"


def test_evidence_graph_emits_readiness_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke: add_readiness fires and produces a typed node."""
    from lynchpin.graph import evidence_graph as eg
    from lynchpin.graph import evidence_system_signals

    end = date(2026, 5, 6)
    target = end + timedelta(days=1)

    fake = ReadinessForecast(
        target_date=target,
        predicted_deep_work_min=100.0,
        confidence_interval_95=(60.0, 140.0),
        inputs={"sleep_hours": 7.0},
        coefficients={"sleep_hours": 15.0},
        intercept=10.0,
        r_squared=0.5,
        sample_n=45,
        caveats=("ok",),
    )
    monkeypatch.setattr(readiness, "build_readiness_forecast", lambda **kwargs: fake)

    nodes: list[eg.EvidenceNode] = []
    evidence_system_signals.add_readiness(nodes, end=end)
    assert nodes
    node = nodes[0]
    assert node.kind == "readiness_forecast"
    assert node.date == target
    assert node.payload["status"] == "available"
