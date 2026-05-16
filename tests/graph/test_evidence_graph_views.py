from datetime import date, datetime, timezone

from lynchpin.core.evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode
from lynchpin.graph.evidence_views import (
    evidence_relations,
    evidence_timeline,
    render_evidence_relations,
    render_evidence_timeline,
)

UTC = timezone.utc


def test_evidence_timeline_orders_timed_rows_before_date_only_and_skips_artifacts():
    day = date(2026, 5, 5)
    graph = EvidenceGraph(
        start=day,
        end=day,
        generated_at=datetime(2026, 5, 5, tzinfo=UTC),
        mode="local-fast",
        nodes=(
            EvidenceNode(
                id="git:sinity-lynchpin:abc",
                kind="commit",
                source="git",
                date=day,
                project="sinity-lynchpin",
                start=datetime(2026, 5, 5, 12, tzinfo=UTC),
                end=datetime(2026, 5, 5, 12, tzinfo=UTC),
                summary="fix: later",
            ),
            EvidenceNode(
                id="terminal:sinity-lynchpin",
                kind="terminal_session",
                source="terminal",
                date=day,
                project="sinity-lynchpin",
                start=datetime(2026, 5, 5, 11, tzinfo=UTC),
                end=datetime(2026, 5, 5, 11, 15, tzinfo=UTC),
                summary="terminal earlier",
            ),
            EvidenceNode(
                id="aw:sinity-lynchpin",
                kind="focus_day",
                source="activitywatch",
                date=day,
                project="sinity-lynchpin",
                summary="daily aggregate",
            ),
            EvidenceNode(
                id="analysis:status:sinity-lynchpin",
                kind="analysis_artifact",
                source="analysis",
                date=day,
                project="sinity-lynchpin",
                summary="status artifact",
            ),
        ),
        edges=(),
        caveats=(),
    )

    rows = evidence_timeline(graph)

    assert [row.node_id for row in rows] == [
        "terminal:sinity-lynchpin",
        "git:sinity-lynchpin:abc",
        "aw:sinity-lynchpin",
    ]


def test_render_evidence_timeline_is_compact_markdown_with_escaped_cells():
    day = date(2026, 5, 5)
    graph = EvidenceGraph(
        start=day,
        end=day,
        generated_at=datetime(2026, 5, 5, tzinfo=UTC),
        mode="local-fast",
        nodes=(
            EvidenceNode(
                id="raw-log:1",
                kind="raw_log",
                source="raw_log",
                date=day,
                project=None,
                start=datetime(2026, 5, 5, 9, tzinfo=UTC),
                end=datetime(2026, 5, 5, 9, tzinfo=UTC),
                summary="one | two",
            ),
        ),
        edges=(),
        caveats=(),
    )

    rendered = render_evidence_timeline(graph)

    assert "| When | Project | Source | Kind | Evidence |" in rendered
    assert "unattributed" in rendered
    assert "one \\| two" in rendered


def test_evidence_relations_project_edges_without_same_day_noise():
    day = date(2026, 5, 5)
    graph = EvidenceGraph(
        start=day,
        end=day,
        generated_at=datetime(2026, 5, 5, tzinfo=UTC),
        mode="local-fast",
        nodes=(
            EvidenceNode(
                id="git:sinity-lynchpin:abc",
                kind="commit",
                source="git",
                date=day,
                project="sinity-lynchpin",
                summary="fix: closes #12",
            ),
            EvidenceNode(
                id="github:sinity-lynchpin:issue:12",
                kind="github_ref",
                source="github",
                date=day,
                project="sinity-lynchpin",
                summary="issue #12",
            ),
            EvidenceNode(
                id="aw:sinity-lynchpin",
                kind="focus_day",
                source="activitywatch",
                date=day,
                project="sinity-lynchpin",
                summary="daily aggregate",
            ),
        ),
        edges=(
            EvidenceEdge(
                source_id="git:sinity-lynchpin:abc",
                target_id="github:sinity-lynchpin:issue:12",
                relation="references",
                evidence="commit subject references issue #12",
                weight=0.9,
            ),
            EvidenceEdge(
                source_id="git:sinity-lynchpin:abc",
                target_id="aw:sinity-lynchpin",
                relation="same_project_day",
                evidence="sinity-lynchpin on 2026-05-05",
                weight=0.4,
            ),
        ),
        caveats=(),
    )

    rows = evidence_relations(graph)
    rendered = render_evidence_relations(graph)

    assert [row.relation for row in rows] == ["references"]
    assert "commit subject references issue #12" in rendered
    assert "same_project_day" not in rendered
