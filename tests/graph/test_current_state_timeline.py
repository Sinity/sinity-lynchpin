"""Tests for the current-state timeline artifact (M.10)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from lynchpin.graph.current_state_timeline import (
    build_current_state_timeline,
    render_current_state_timeline,
)
from lynchpin.core.evidence_graph import EvidenceGraph, EvidenceNode

UTC = timezone.utc


def _graph(nodes: list[EvidenceNode], *, start: date, end: date) -> EvidenceGraph:
    return EvidenceGraph(
        start=start,
        end=end,
        generated_at=datetime(2026, 5, 7, tzinfo=UTC),
        nodes=tuple(nodes),
        edges=(),
        caveats=(),
    )


def _node(*, kind: str, source: str, day: date, project: str = "demo",
          when: datetime | None = None, summary: str = "", payload: dict | None = None,
          node_id: str | None = None) -> EvidenceNode:
    return EvidenceNode(
        id=node_id or f"{source}:{kind}:{day.isoformat()}:{summary[:8]}",
        kind=kind,  # type: ignore[arg-type]
        source=source,
        date=day,
        project=project,
        start=when,
        end=when,
        summary=summary or kind,
        payload=payload or {},
    )


def test_timeline_groups_events_by_day():
    base = datetime(2026, 5, 7, 14, tzinfo=UTC)
    nodes = [
        _node(kind="commit", source="git", day=date(2026, 5, 7), when=base,
              summary="feat: x (#5)",
              payload={"commit": "abc123def456", "github_refs": {"prs": [5], "issues": []},
                       "paths": ("src/foo.py",), "files_changed": 1}),
        _node(kind="ai_work_event", source="polylogue",
              day=date(2026, 5, 7), when=base - timedelta(hours=1),
              summary="Implement foo",
              payload={"event_id": "we1", "kind": "implementation", "kind_tier": "high",
                       "kind_source": "agreement", "duration_ms": 30 * 60_000,
                       "file_paths": ["/realm/project/demo/src/foo.py"]}),
        _node(kind="commit", source="git", day=date(2026, 5, 8),
              when=base + timedelta(days=1),
              summary="fix: y", payload={"commit": "def789", "github_refs": {}, "paths": (), "files_changed": 1}),
    ]
    timeline = build_current_state_timeline(
        _graph(nodes, start=date(2026, 5, 7), end=date(2026, 5, 8)),
        start=date(2026, 5, 7), end=date(2026, 5, 8),
    )
    assert len(timeline.days) == 2
    assert timeline.days[0].day == date(2026, 5, 7)
    assert timeline.days[1].day == date(2026, 5, 8)
    assert len(timeline.days[0].rows) == 2  # commit + ai_work_event
    assert len(timeline.days[1].rows) == 1


def test_timeline_excludes_out_of_window_events():
    base = datetime(2026, 5, 7, 14, tzinfo=UTC)
    nodes = [
        _node(kind="commit", source="git", day=date(2026, 4, 15),
              when=base - timedelta(days=22), summary="too early",
              payload={"commit": "old", "github_refs": {}, "paths": ()}),
        _node(kind="commit", source="git", day=date(2026, 5, 7),
              when=base, summary="in window",
              payload={"commit": "new", "github_refs": {}, "paths": ()}),
    ]
    timeline = build_current_state_timeline(
        _graph(nodes, start=date(2026, 5, 1), end=date(2026, 5, 7)),
        start=date(2026, 5, 1), end=date(2026, 5, 7),
    )
    assert len(timeline.days) == 1
    assert "in window" in timeline.days[0].rows[0].summary


def test_timeline_separates_temporal_signals_from_event_rows():
    base = datetime(2026, 5, 7, 14, tzinfo=UTC)
    nodes = [
        _node(kind="temporal_anomaly", source="temporal", day=date(2026, 5, 7),
              when=base, summary="Spike in commit volume",
              payload={"score": 3.2, "metric": "commits"}),
        _node(kind="commit", source="git", day=date(2026, 5, 7), when=base,
              summary="feat: x",
              payload={"commit": "abc", "github_refs": {}, "paths": ()}),
    ]
    timeline = build_current_state_timeline(
        _graph(nodes, start=date(2026, 5, 7), end=date(2026, 5, 7)),
        start=date(2026, 5, 7), end=date(2026, 5, 7),
    )
    section = timeline.days[0]
    assert len(section.signals) == 1
    assert len(section.rows) == 1
    assert section.signals[0].kind == "temporal_anomaly"


def test_render_includes_citation_for_commit_with_refs():
    base = datetime(2026, 5, 7, 14, tzinfo=UTC)
    nodes = [
        _node(kind="commit", source="git", day=date(2026, 5, 7), when=base,
              summary="feat: implement #5",
              payload={"commit": "abc12345def", "github_refs": {"prs": [5], "issues": [3]},
                       "paths": (), "files_changed": 1}),
    ]
    timeline = build_current_state_timeline(
        _graph(nodes, start=date(2026, 5, 7), end=date(2026, 5, 7)),
        start=date(2026, 5, 7), end=date(2026, 5, 7),
    )
    md = render_current_state_timeline(timeline)
    assert "abc12345" in md
    assert "pr#5" in md
    assert "issue#3" in md


def test_render_includes_kind_tier_for_ai_work_event():
    base = datetime(2026, 5, 7, 14, tzinfo=UTC)
    nodes = [
        _node(kind="ai_work_event", source="polylogue", day=date(2026, 5, 7), when=base,
              summary="implementation",
              payload={"event_id": "we1", "kind": "debugging", "kind_tier": "low",
                       "duration_ms": 5 * 60_000, "file_paths": []}),
    ]
    timeline = build_current_state_timeline(
        _graph(nodes, start=date(2026, 5, 7), end=date(2026, 5, 7)),
        start=date(2026, 5, 7), end=date(2026, 5, 7),
    )
    md = render_current_state_timeline(timeline)
    assert "debugging[low]" in md


def test_empty_window_renders_no_evidence_message():
    timeline = build_current_state_timeline(
        _graph([], start=date(2026, 5, 7), end=date(2026, 5, 7)),
        start=date(2026, 5, 7), end=date(2026, 5, 7),
    )
    assert timeline.days == ()
    assert timeline.total_rows == 0
    md = render_current_state_timeline(timeline)
    assert "No evidence in the requested window" in md


def test_analysis_artifact_nodes_are_excluded():
    base = datetime(2026, 5, 7, 14, tzinfo=UTC)
    nodes = [
        _node(kind="analysis_artifact", source="analysis", day=date(2026, 5, 7),
              when=base, summary="active_work_packages.json",
              payload={"name": "active_work_packages.json"}),
        _node(kind="commit", source="git", day=date(2026, 5, 7), when=base,
              summary="real event",
              payload={"commit": "abc", "github_refs": {}, "paths": ()}),
    ]
    timeline = build_current_state_timeline(
        _graph(nodes, start=date(2026, 5, 7), end=date(2026, 5, 7)),
        start=date(2026, 5, 7), end=date(2026, 5, 7),
    )
    section = timeline.days[0]
    assert len(section.rows) == 1
    assert section.rows[0].kind == "commit"
