"""Tests for composite.temporal_signals."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from lynchpin.composite.temporal_signals import (
    SignalSpec,
    detect_temporal_signals,
)


def _series(start: date, n: int, values: list[float]) -> dict[date, float]:
    assert len(values) == n
    return {start + timedelta(days=i): v for i, v in enumerate(values)}


def test_changepoint_detected_when_mean_shifts() -> None:
    history_start = date(2026, 4, 1)
    window_start = date(2026, 4, 29)  # 28 days of baseline before
    end = date(2026, 5, 30)            # 32 days in window
    n = (end - history_start).days + 1

    # Flat 10 across history; in window: first 16 days at 10, then jump to 50.
    values = [10.0] * 28 + [10.0] * 16 + [50.0] * 16
    series = _series(history_start, n, values)

    spec = SignalSpec("test_signal", "synthetic", lambda s, e: series)
    events = detect_temporal_signals(start=window_start, end=end, specs=(spec,))

    cps = [e for e in events if e.kind == "temporal_changepoint"]
    assert cps, "expected at least one changepoint"
    assert cps[0].payload["direction"] == "up"
    assert cps[0].payload["after_mean"] > cps[0].payload["before_mean"]


def test_anomaly_detected_against_prior_baseline() -> None:
    history_start = date(2026, 4, 1)
    window_start = date(2026, 4, 29)
    end = date(2026, 5, 5)
    n = (end - history_start).days + 1

    # Noisy baseline so IQR > 0; one extreme spike on day index 31 (2026-05-02).
    rng = [9.0, 11.0, 10.0, 9.5, 10.5, 10.2, 9.8] * 4
    values = rng[:28] + [10.0, 10.0, 10.0, 200.0, 10.0, 10.0, 10.0]
    series = _series(history_start, n, values)

    spec = SignalSpec("spike", "synthetic", lambda s, e: series)
    events = detect_temporal_signals(start=window_start, end=end, specs=(spec,))

    anomalies = [e for e in events if e.kind == "temporal_anomaly"]
    assert any(e.event_date == date(2026, 5, 2) for e in anomalies)
    spike = next(e for e in anomalies if e.event_date == date(2026, 5, 2))
    assert spike.payload["direction"] == "high"
    assert spike.payload["value"] == 200.0


def test_trend_detected_for_monotonic_increase() -> None:
    history_start = date(2026, 4, 1)
    window_start = date(2026, 4, 29)
    end = date(2026, 5, 28)
    n = (end - history_start).days + 1

    # Flat baseline, then strict increase across the window.
    in_window_n = (end - window_start).days + 1
    values = [10.0] * 28 + [float(i + 1) for i in range(in_window_n)]
    series = _series(history_start, n, values)

    spec = SignalSpec("rising", "synthetic", lambda s, e: series)
    events = detect_temporal_signals(start=window_start, end=end, specs=(spec,))

    trends = [e for e in events if e.kind == "temporal_trend"]
    assert trends and trends[0].payload["direction"] == "rising"
    assert trends[0].payload["slope"] > 0


def test_no_events_for_flat_series() -> None:
    history_start = date(2026, 4, 1)
    window_start = date(2026, 4, 29)
    end = date(2026, 5, 28)
    n = (end - history_start).days + 1

    values = [5.0] * n
    series = _series(history_start, n, values)
    spec = SignalSpec("flat", "synthetic", lambda s, e: series)

    events = detect_temporal_signals(start=window_start, end=end, specs=(spec,))
    assert not [e for e in events if e.kind == "temporal_anomaly"]
    assert not [e for e in events if e.kind == "temporal_trend"]
    assert not [e for e in events if e.kind == "temporal_changepoint"]


def test_loader_failure_is_silent() -> None:
    def bad_loader(s: date, e: date) -> dict[date, float]:
        raise RuntimeError("boom")

    spec = SignalSpec("broken", "synthetic", bad_loader)
    events = detect_temporal_signals(
        start=date(2026, 5, 1), end=date(2026, 5, 7), specs=(spec,)
    )
    assert events == ()


def test_evidence_graph_includes_temporal_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke test that _add_temporal_signals fires when wired into the graph."""
    from lynchpin.composite import evidence_graph as eg

    history_start = date(2026, 4, 1)
    window_start = date(2026, 4, 29)
    end = date(2026, 5, 5)
    # Noisy baseline so IQR > 0; one extreme spike on day index 31 (2026-05-02).
    rng = [9.0, 11.0, 10.0, 9.5, 10.5, 10.2, 9.8] * 4
    values = rng[:28] + [10.0, 10.0, 10.0, 200.0, 10.0, 10.0, 10.0]
    series = {history_start + timedelta(days=i): v for i, v in enumerate(values)}

    fake_specs = (SignalSpec("test_spike", "synthetic", lambda s, e: series),)

    import lynchpin.composite.temporal_signals as ts

    monkeypatch.setattr(ts, "default_signal_specs", lambda: fake_specs)

    nodes: list[eg.EvidenceNode] = []
    eg._add_temporal_signals(nodes, start=window_start, end=end)
    kinds = {n.kind for n in nodes}
    assert "temporal_anomaly" in kinds
