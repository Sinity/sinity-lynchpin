"""Tests for the substrate confidence matrix (M.17)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from lynchpin.core.evidence import (
    SourceReadiness,
    SourceReadinessReport,
)
from lynchpin.core.evidence_graph import EvidenceGraph, EvidenceNode
from lynchpin.graph.substrate_confidence import (
    build_substrate_confidence_matrix,
    render_substrate_confidence_matrix,
)
from lynchpin.graph.work_correlation import CorrelatedWorkDay

UTC = timezone.utc


def _readiness(*sources: SourceReadiness, start=date(2026, 5, 1), end=date(2026, 5, 7)) -> SourceReadinessReport:
    return SourceReadinessReport(
        start=start, end=end,
        generated_at=datetime(2026, 5, 7, tzinfo=UTC),
        sources=tuple(sources),
    )


def _graph(nodes: list[EvidenceNode] = None) -> EvidenceGraph:
    return EvidenceGraph(
        start=date(2026, 5, 1), end=date(2026, 5, 7),
        generated_at=datetime(2026, 5, 7, tzinfo=UTC),
        mode="materialized",
        nodes=tuple(nodes or []),
        edges=(),
        caveats=(),
    )


def _ai_work_event(*, kind_tier: str, day: date = date(2026, 5, 7)) -> EvidenceNode:
    return EvidenceNode(
        id=f"polylogue:we:{kind_tier}:{day.isoformat()}",
        kind="ai_work_event",
        source="polylogue",
        date=day,
        project="demo",
        summary="x",
        payload={"kind": "implementation", "kind_tier": kind_tier},
    )


def _correlated_day(*, project: str, day: date, sources: list[str]) -> CorrelatedWorkDay:
    return CorrelatedWorkDay(
        date=day,
        project=project,
        commit_count=1 if "git" in sources else 0,
        commit_shas=("abc",) if "git" in sources else (),
        commit_subjects=("x",) if "git" in sources else (),
        github_refs=(),
        github_lifecycles={},
        ai_session_count=1 if "polylogue" in sources else 0,
        ai_conversation_ids=("c1",) if "polylogue" in sources else (),
        raw_log_count=1 if "raw_log" in sources else 0,
        raw_log_refs=("a",) if "raw_log" in sources else (),
        focus_minutes=60.0 if "activitywatch" in sources else 0.0,
        shell_minutes=10.0 if "terminal" in sources else 0.0,
        shell_command_count=1 if "terminal" in sources else 0,
        sources=tuple(sorted(sources)),
    )


def test_coverage_high_when_source_available():
    readiness = _readiness(
        SourceReadiness(source="git", status="available", reason="ok",
                        cost="materialized", last_date=date(2026, 5, 7)),
    )
    matrix = build_substrate_confidence_matrix(readiness=readiness, graph=_graph(), correlation_rows=())
    git_row = next(r for r in matrix.rows if r.layer == "git")
    assert git_row.coverage.tier == "high"
    assert git_row.coverage.detail == "available"


def test_coverage_low_when_source_missing():
    readiness = _readiness(
        SourceReadiness(source="sleep", status="missing", reason="export not found",
                        cost="materialized"),
    )
    matrix = build_substrate_confidence_matrix(readiness=readiness, graph=_graph(), correlation_rows=())
    sleep_row = next(r for r in matrix.rows if r.layer == "sleep")
    assert sleep_row.coverage.tier == "low"


def test_date_coverage_high_when_bounds_cover_window():
    readiness = _readiness(
        SourceReadiness(source="git", status="available", reason="ok",
                        cost="materialized", first_date=date(2026, 5, 1),
                        last_date=date(2026, 5, 7)),
    )
    matrix = build_substrate_confidence_matrix(readiness=readiness, graph=_graph(), correlation_rows=())
    git_row = next(r for r in matrix.rows if r.layer == "git")
    assert git_row.date_coverage.tier == "high"


def test_date_coverage_low_when_bounds_miss_window():
    readiness = _readiness(
        SourceReadiness(source="spotify", status="available", reason="ok",
                        cost="materialized", last_date=date(2026, 3, 1)),
    )
    matrix = build_substrate_confidence_matrix(readiness=readiness, graph=_graph(), correlation_rows=())
    spotify_row = next(r for r in matrix.rows if r.layer == "spotify")
    assert spotify_row.date_coverage.tier == "low"


def test_kind_quality_high_when_majority_high_tier():
    nodes = [_ai_work_event(kind_tier="high") for _ in range(8)]
    nodes += [_ai_work_event(kind_tier="medium") for _ in range(2)]
    readiness = _readiness(
        SourceReadiness(source="polylogue", status="available", reason="ok",
                        cost="materialized", last_date=date(2026, 5, 7)),
    )
    matrix = build_substrate_confidence_matrix(readiness=readiness, graph=_graph(nodes), correlation_rows=())
    poly_row = next(r for r in matrix.rows if r.layer == "polylogue")
    assert poly_row.kind_quality.tier == "high"
    assert "8/10" in poly_row.kind_quality.detail


def test_kind_quality_low_when_few_high_tier():
    nodes = [_ai_work_event(kind_tier="low") for _ in range(8)]
    nodes += [_ai_work_event(kind_tier="high") for _ in range(2)]
    readiness = _readiness(
        SourceReadiness(source="polylogue", status="available", reason="ok",
                        cost="materialized", last_date=date(2026, 5, 7)),
    )
    matrix = build_substrate_confidence_matrix(readiness=readiness, graph=_graph(nodes), correlation_rows=())
    poly_row = next(r for r in matrix.rows if r.layer == "polylogue")
    assert poly_row.kind_quality.tier == "low"


def test_kind_quality_n_a_for_non_polylogue_layer():
    readiness = _readiness(
        SourceReadiness(source="git", status="available", reason="ok",
                        cost="materialized", last_date=date(2026, 5, 7)),
    )
    matrix = build_substrate_confidence_matrix(readiness=readiness, graph=_graph(), correlation_rows=())
    git_row = next(r for r in matrix.rows if r.layer == "git")
    assert git_row.kind_quality.tier == "n_a"


def test_cross_source_high_when_layer_co_occurs_most_days():
    readiness = _readiness(
        SourceReadiness(source="git", status="available", reason="ok",
                        cost="materialized", last_date=date(2026, 5, 7)),
    )
    rows = [
        _correlated_day(project="demo", day=date(2026, 5, d), sources=["git", "polylogue"])
        for d in range(1, 8)
    ]
    matrix = build_substrate_confidence_matrix(readiness=readiness, graph=_graph(), correlation_rows=rows)
    git_row = next(r for r in matrix.rows if r.layer == "git")
    assert git_row.cross_source.tier == "high"


def test_cross_source_low_when_layer_isolated():
    readiness = _readiness(
        SourceReadiness(source="git", status="available", reason="ok",
                        cost="materialized", last_date=date(2026, 5, 7)),
    )
    # All rows are git-only — no co-occurrence with other sources.
    rows = [
        _correlated_day(project="demo", day=date(2026, 5, d), sources=["git"])
        for d in range(1, 8)
    ]
    matrix = build_substrate_confidence_matrix(readiness=readiness, graph=_graph(), correlation_rows=rows)
    git_row = next(r for r in matrix.rows if r.layer == "git")
    assert git_row.cross_source.tier == "low"


def test_render_includes_overall_tier_and_layers():
    readiness = _readiness(
        SourceReadiness(source="git", status="available", reason="ok",
                        cost="materialized", last_date=date(2026, 5, 7)),
        SourceReadiness(source="polylogue", status="available", reason="ok",
                        cost="materialized", last_date=date(2026, 5, 7)),
    )
    nodes = [_ai_work_event(kind_tier="high") for _ in range(10)]
    rows = [_correlated_day(project="demo", day=date(2026, 5, 1), sources=["git", "polylogue"])]
    matrix = build_substrate_confidence_matrix(readiness=readiness, graph=_graph(nodes), correlation_rows=rows)
    rendered = render_substrate_confidence_matrix(matrix)
    assert "Overall substrate confidence" in rendered
    assert "git" in rendered
    assert "polylogue" in rendered
    assert "Coverage" in rendered
