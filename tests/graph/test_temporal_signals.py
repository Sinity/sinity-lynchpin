"""Tests for composite.temporal_signals."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from lynchpin.graph.temporal_signals import (
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


def test_default_activitywatch_signal_loaders_read_derived_products(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lynchpin.graph.temporal_signals as ts

    calls: list[tuple[str, bool]] = []

    def fake_deep_work(*, start, end, ensure=True):
        calls.append(("deep_work", ensure))
        return (
            SimpleNamespace(start=start, duration_min=45.0),
        )

    def fake_circadian(*, start, end, ensure=True):
        calls.append(("circadian", ensure))
        return (
            SimpleNamespace(date=start, active_min=120.0),
        )

    def fake_fragmentation(*, start, end, ensure=True):
        calls.append(("fragmentation", ensure))
        return (
            SimpleNamespace(date=start, fragmentation=0.25),
        )

    monkeypatch.setattr(
        "lynchpin.sources.activitywatch_derived.iter_derived_deep_work",
        fake_deep_work,
    )
    monkeypatch.setattr(
        "lynchpin.sources.activitywatch_derived.iter_derived_circadian",
        fake_circadian,
    )
    monkeypatch.setattr(
        "lynchpin.sources.activitywatch_derived.iter_derived_fragmentation",
        fake_fragmentation,
    )

    assert ts._load_deep_work(date(2026, 4, 1), date(2026, 5, 30), ensure=False) == {
        date(2026, 4, 1): 45.0
    }
    assert ts._load_active_hours(date(2026, 4, 1), date(2026, 5, 30), ensure=False) == {
        date(2026, 4, 1): 2.0
    }
    assert ts._load_fragmentation(date(2026, 4, 1), date(2026, 5, 30), ensure=False) == {
        date(2026, 4, 1): 0.25
    }

    assert calls == [
        ("deep_work", False),
        ("circadian", False),
        ("fragmentation", False),
    ]


def test_commit_loader_uses_materialized_counts_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lynchpin.graph.temporal_signals as ts

    monkeypatch.setattr(
        ts,
        "_load_commit_counts_from_substrate",
        lambda start, end: {start: 2.0},
    )

    def fail_live_git(**_kwargs):
        raise AssertionError("live git should not be scanned when substrate covers the window")

    monkeypatch.setattr("lynchpin.sources.git.daily_activity", fail_live_git)

    assert ts._load_commits(date(2026, 5, 1), date(2026, 5, 3)) == {
        date(2026, 5, 1): 2.0
    }


def test_commit_loader_falls_back_to_live_git_without_materialized_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lynchpin.graph.temporal_signals as ts

    monkeypatch.setattr(ts, "_load_commit_counts_from_substrate", lambda start, end: None)
    monkeypatch.setattr(
        "lynchpin.sources.git.daily_activity",
        lambda *, start, end: (
            SimpleNamespace(date=start, commit_count=1),
            SimpleNamespace(date=start, commit_count=3),
        ),
    )

    assert ts._load_commits(date(2026, 5, 1), date(2026, 5, 3)) == {
        date(2026, 5, 1): 4.0
    }


def test_commit_source_status_requires_half_open_window_coverage() -> None:
    import lynchpin.graph.temporal_signals as ts

    class Conn:
        def __init__(self, row):
            self.row = row

        def execute(self, _sql, _params):
            return self

        def fetchone(self):
            return self.row

    assert ts._commit_source_status_covers(
        Conn((date(2026, 5, 1), date(2026, 5, 4))),
        refresh_id="r1",
        start=date(2026, 5, 1),
        end=date(2026, 5, 3),
    )
    assert not ts._commit_source_status_covers(
        Conn((date(2026, 5, 1), date(2026, 5, 3))),
        refresh_id="r1",
        start=date(2026, 5, 1),
        end=date(2026, 5, 3),
    )
    assert not ts._commit_source_status_covers(
        Conn(None),
        refresh_id="r1",
        start=date(2026, 5, 1),
        end=date(2026, 5, 3),
    )


def test_evidence_graph_includes_temporal_product_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke test that add_temporal_signals reads the converged product."""
    from lynchpin.graph import evidence_graph as eg
    from lynchpin.graph import evidence_system_signals

    window_start = date(2026, 4, 29)
    end = date(2026, 5, 5)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", lambda name, *, window: None)
    monkeypatch.setattr(
        "lynchpin.sources.temporal_signals.iter_temporal_signals",
        lambda *, start, end, ensure=True: (
            SimpleNamespace(
                kind="temporal_anomaly",
                signal="test_spike",
                event_date=date(2026, 5, 2),
                summary="spike",
                payload={"value": 200.0},
            ),
        ),
    )

    nodes: list[eg.EvidenceNode] = []
    evidence_system_signals.add_temporal_signals(nodes, start=window_start, end=end)
    kinds = {n.kind for n in nodes}
    assert "temporal_anomaly" in kinds


def test_materialized_temporal_signals_ensure_and_read_product(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin.graph import evidence_system_signals

    calls: list[tuple[str, tuple[date, date]]] = []
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window: calls.append((name, window)),
    )
    monkeypatch.setattr(
        "lynchpin.sources.temporal_signals.iter_temporal_signals",
        lambda *, start, end, ensure=True: (
            SimpleNamespace(
                kind="temporal_anomaly",
                signal="deep_work_min",
                event_date=date(2026, 5, 2),
                summary="spike",
                payload={"value": 10},
            ),
        ),
    )

    nodes = []
    evidence_system_signals.add_temporal_signals(
        nodes,
        start=date(2026, 5, 1),
        end=date(2026, 5, 2),
    )

    assert calls == [("temporal_signals", (date(2026, 5, 1), date(2026, 5, 3)))]
    assert [node.kind for node in nodes] == ["temporal_anomaly"]
