from datetime import date, datetime, timezone

from lynchpin.core.evidence import EvidenceCaveat
from lynchpin.core.evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode
from lynchpin.graph.weak_tags import (
    build_weak_tags,
    render_weak_tag_summary,
    save_weak_tags,
)


UTC = timezone.utc


def _node(
    node_id: str,
    kind: str,
    *,
    source: str,
    summary: str,
    project: str | None = "sinity-lynchpin",
    payload: dict | None = None,
) -> EvidenceNode:
    return EvidenceNode(
        id=node_id,
        kind=kind,
        source=source,
        date=date(2026, 5, 5),
        project=project,
        summary=summary,
        start=datetime(2026, 5, 5, 12, tzinfo=UTC),
        end=datetime(2026, 5, 5, 12, 5, tzinfo=UTC),
        payload=payload or {},
    )


def _graph() -> EvidenceGraph:
    nodes = (
        _node(
            "git:sinity-lynchpin:a",
            "commit",
            source="git",
            summary="feat(current-state): add evidence graph",
            payload={"files_changed": 3, "lines_changed": 120, "github_refs": {"prs": [1], "issues": [2]}},
        ),
        _node(
            "polylogue:conv-1:sinity-lynchpin",
            "ai_session",
            source="polylogue",
            summary="Discuss current-state weak-tag analysis",
            payload={"message_count": 8, "tool_use_count": 2, "work_event_kind": "implementation"},
        ),
        _node(
            "raw-log:2026-05-05T12:04",
            "raw_log",
            source="knowledgebase",
            summary="Decision: use weak-tag analysis on the critical path",
            payload={"text": "Decision: use weak-tag analysis on the critical path"},
        ),
    )
    edges = (
        EvidenceEdge(nodes[0].id, nodes[1].id, "same_project_day", "same project/day", 0.5),
        EvidenceEdge(nodes[1].id, nodes[2].id, "temporal_overlap", "overlaps", 0.7),
    )
    return EvidenceGraph(
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        generated_at=datetime(2026, 5, 5, tzinfo=UTC),
        nodes=nodes,
        edges=edges,
        caveats=(EvidenceCaveat("source", "partial", "test caveat"),),
    )


def test_weak_tags_builds_annotations_clusters_and_moments():
    enrichment = build_weak_tags(_graph())

    labels = {annotation.label for annotation in enrichment.annotations}
    assert "code_feat" in labels
    assert "github_referenced_change" in labels
    assert "ai_assisted_work" in labels
    assert "decision_signal" in labels

    assert len(enrichment.clusters) == 1
    cluster = enrichment.clusters[0]
    assert cluster.support_sources == ("git", "knowledgebase", "polylogue")
    assert cluster.score > 4

    assert len(enrichment.moments) == 1
    assert enrichment.moments[0].title == "sinity-lynchpin: decision or commitment"
    assert "sinity-lynchpin" in render_weak_tag_summary(enrichment)


def test_weak_tags_classifies_analysis_artifacts():
    graph = EvidenceGraph(
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        generated_at=datetime(2026, 5, 5, tzinfo=UTC),
        nodes=(
            _node(
                "analysis:sinex_structure_metrics.json",
                "analysis_artifact",
                source="analysis",
                summary="sinex_structure_metrics.json",
                project="sinex",
                payload={"kind": "json", "top_level_keys": ("totals",)},
            ),
        ),
        edges=(),
        caveats=(),
    )

    enrichment = build_weak_tags(graph)

    assert {annotation.label for annotation in enrichment.annotations} == {"generated_analysis_product"}


def test_weak_tags_clusters_temporal_proximity_edges():
    left = _node("git:a", "commit", source="git", summary="fix: nearby")
    right = _node("terminal:a", "terminal_session", source="terminal", summary="1 command")
    graph = EvidenceGraph(
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        generated_at=datetime(2026, 5, 5, tzinfo=UTC),
        nodes=(left, right),
        edges=(EvidenceEdge(left.id, right.id, "temporal_proximity", "git within 5m of terminal", 0.82),),
        caveats=(),
    )

    enrichment = build_weak_tags(graph)

    assert len(enrichment.clusters) == 1
    assert enrichment.clusters[0].support_sources == ("git", "terminal")


def test_weak_tags_persistence_uses_configured_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "lynchpin.graph.weak_tags._weak_tags_db_path",
        lambda: tmp_path / "weak_tags.sqlite3",
    )
    enrichment = build_weak_tags(_graph())

    save_weak_tags(enrichment)

    assert (tmp_path / "weak_tags.sqlite3").exists()
