"""Report builders must not double-count rollups or depend on input order."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from lynchpin.core.evidence_graph import EvidenceGraph
from lynchpin.graph.work_correlation import correlate_work_days, work_day_correlations

UTC = timezone.utc
DAY = date(2026, 6, 6)


def _stub_activitywatch(monkeypatch: pytest.MonkeyPatch, *, spans=(), focus_days=(), hourly=()) -> None:
    from lynchpin.graph import evidence_activitywatch

    monkeypatch.setattr(evidence_activitywatch, "focus_spans", lambda **kwargs: tuple(spans))
    monkeypatch.setattr(evidence_activitywatch, "project_focus_days", lambda **kwargs: tuple(focus_days))
    monkeypatch.setattr(evidence_activitywatch, "circadian", lambda **kwargs: tuple(hourly))
    for name in ("deep_work", "loops", "fragmentation", "attention"):
        monkeypatch.setattr(evidence_activitywatch, name, lambda **kwargs: ())
    monkeypatch.setattr(evidence_activitywatch, "ensure_activitywatch_derived", lambda **kwargs: None)


def _activitywatch_nodes(monkeypatch: pytest.MonkeyPatch, **products) -> list:
    from lynchpin.graph import evidence_activitywatch

    _stub_activitywatch(monkeypatch, **products)
    nodes: list = []
    evidence_activitywatch.add_focus(nodes, start=DAY, end=DAY, selected=set())
    return nodes


def _graph(nodes) -> EvidenceGraph:
    return EvidenceGraph(
        start=DAY,
        end=DAY,
        generated_at=datetime(2026, 6, 7, tzinfo=UTC),
        nodes=tuple(nodes),
        edges=(),
        caveats=(),
    )


def _span(duration_s: float) -> SimpleNamespace:
    return SimpleNamespace(
        start=datetime(2026, 6, 6, 10, tzinfo=UTC),
        end=datetime(2026, 6, 6, 11, tzinfo=UTC),
        kind="focused",
        app="kitty",
        title="lynchpin",
        mode="coding",
        project="lynchpin",
        duration_s=duration_s,
        keypress_count=0,
        keylog_state="available",
    )


def _hour(hour: int, active_min: float, project: str) -> SimpleNamespace:
    return SimpleNamespace(
        date=DAY,
        hour=hour,
        active_min=active_min,
        recovery_min=0.0,
        dominant_mode="coding",
        dominant_project=project,
    )


def test_focus_rollup_does_not_add_to_its_own_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = _activitywatch_nodes(
        monkeypatch,
        spans=[_span(3600.0)],
        focus_days=[SimpleNamespace(date=DAY, project="lynchpin", duration_s=3600.0)],
    )
    # Both raw observations stay in the graph.
    assert sorted(node.kind for node in nodes) == ["focus_day", "focus_span"]

    rows = work_day_correlations(start=DAY, end=DAY, graph=_graph(nodes))

    assert [(row.project, row.focus_minutes) for row in rows] == [("sinity-lynchpin", 60.0)]


def test_focus_rollup_is_the_total_when_spans_are_filtered(monkeypatch: pytest.MonkeyPatch) -> None:
    # Spans under a minute are dropped from the detail view; the rollup is complete.
    nodes = _activitywatch_nodes(
        monkeypatch,
        spans=[_span(1800.0)],
        focus_days=[SimpleNamespace(date=DAY, project="lynchpin", duration_s=2400.0)],
    )

    rows = work_day_correlations(start=DAY, end=DAY, graph=_graph(nodes))

    assert rows[0].focus_minutes == 40.0


def test_direct_correlation_resolves_span_and_rollup_inputs() -> None:
    rows = correlate_work_days(
        focus_spans=[
            SimpleNamespace(project="lynchpin", start=datetime(2026, 6, 6, 10, tzinfo=UTC), duration_s=3600),
            SimpleNamespace(project="lynchpin", date=DAY, duration_s=3600),
        ]
    )

    assert rows[0].focus_minutes == 60.0


def test_circadian_peak_is_independent_of_hourly_row_order(monkeypatch: pytest.MonkeyPatch) -> None:
    hourly = [_hour(9, 20.0, "polylogue"), _hour(14, 45.0, "lynchpin"), _hour(22, 45.0, "sinex")]

    forward = _activitywatch_nodes(monkeypatch, hourly=hourly)
    backward = _activitywatch_nodes(monkeypatch, hourly=list(reversed(hourly)))

    assert forward == backward
    (node,) = [node for node in forward if node.kind == "circadian_profile"]
    # Tie at 45 minutes resolves to the earlier hour.
    assert node.payload["peak_hour"] == 14
    assert node.project == "sinity-lynchpin"
    assert node.payload["active_min"] == 110.0
    assert node.payload["hours_observed"] == 3
