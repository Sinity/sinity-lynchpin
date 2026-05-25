"""Tests for project-relationship graph (M.11)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from lynchpin.core.evidence_graph import EvidenceGraph, EvidenceNode
from lynchpin.graph.project_relationships import (
    build_project_relationships,
    render_project_relationships,
)

UTC = timezone.utc


def _graph(nodes: list[EvidenceNode]) -> EvidenceGraph:
    return EvidenceGraph(
        start=date(2026, 5, 1), end=date(2026, 5, 7),
        generated_at=datetime(2026, 5, 7, tzinfo=UTC),
        nodes=tuple(nodes),
        edges=(),
        caveats=(),
    )


def _ai_session(*, conv_id: str, project: str, day: date = date(2026, 5, 7)) -> EvidenceNode:
    return EvidenceNode(
        id=f"polylogue:{conv_id}:{project}",
        kind="ai_session",
        source="polylogue",
        date=day,
        project=project,
        summary="x",
        payload={"conversation_id": conv_id, "provider": "claude-code"},
    )


def _ai_work_event(*, event_id: str, project: str, day: date = date(2026, 5, 7)) -> EvidenceNode:
    return EvidenceNode(
        id=f"polylogue:we:{event_id}:{project}",
        kind="ai_work_event",
        source="polylogue",
        date=day,
        project=project,
        summary="x",
        payload={"event_id": event_id, "kind": "implementation", "kind_tier": "high"},
    )


def _commit(*, sha: str, project: str, prs: list[int] = None, issues: list[int] = None,
            day: date = date(2026, 5, 7)) -> EvidenceNode:
    return EvidenceNode(
        id=f"git:{project}:{sha}",
        kind="commit",
        source="git",
        date=day,
        project=project,
        summary="x",
        payload={"commit": sha, "github_refs": {"prs": prs or [], "issues": issues or []}, "paths": ()},
    )


def test_shared_ai_session_creates_edge():
    nodes = [
        _ai_session(conv_id="c1", project="alpha"),
        _ai_session(conv_id="c1", project="beta"),
    ]
    rg = build_project_relationships(_graph(nodes))
    assert len(rg.relationships) == 1
    rel = rg.relationships[0]
    assert (rel.project_a, rel.project_b) == ("alpha", "beta")
    assert rel.signal_counts.get("shared_ai_sessions") == 1


def test_shared_ai_work_event_creates_higher_weight_edge():
    nodes = [
        _ai_work_event(event_id="e1", project="alpha"),
        _ai_work_event(event_id="e1", project="beta"),
    ]
    rg = build_project_relationships(_graph(nodes))
    assert rg.relationships[0].signal_counts.get("shared_ai_work_events") == 1
    # Work-event edges are weighted higher (1.5) than session edges (1.0).
    assert rg.relationships[0].weight >= 1.5


def test_bare_pr_number_collision_is_not_a_coordination_signal():
    """Two projects that happen to have the same PR number are NOT
    coordinating — they're independently numbered. Verified false-positive:
    polylogue#542 (flake deps) and sinex#542 (architecture docs) are
    unrelated. Until the extractor preserves owner/repo#N form, bare ``#N``
    cross-project collisions must not emit a shared_commits signal."""
    nodes = [
        _commit(sha="abc", project="alpha", prs=[5]),
        _commit(sha="def", project="beta", prs=[5]),
    ]
    rg = build_project_relationships(_graph(nodes))
    assert rg.relationships == ()


def test_no_edge_when_projects_dont_share_signals():
    nodes = [
        _ai_session(conv_id="c1", project="alpha"),
        _ai_session(conv_id="c2", project="beta"),  # different conversations
    ]
    rg = build_project_relationships(_graph(nodes))
    assert rg.relationships == ()


def test_multiple_signals_compound_weight():
    nodes = [
        _ai_session(conv_id="c1", project="alpha"),
        _ai_session(conv_id="c1", project="beta"),
        _ai_work_event(event_id="e1", project="alpha"),
        _ai_work_event(event_id="e1", project="beta"),
    ]
    rg = build_project_relationships(_graph(nodes))
    rel = rg.relationships[0]
    # 1.0 (session) + 1.5 (work_event) = 2.5
    assert rel.weight >= 2.0
    assert set(rel.signal_counts.keys()) == {"shared_ai_sessions", "shared_ai_work_events"}


def test_sample_evidence_node_ids_stay_within_the_pair():
    """Sessions touching N>2 projects must not leak off-pair node ids into
    every pair's sample list. Previously a session touching {alpha, beta,
    gamma, delta} dumped all 4 node ids into the alpha-beta sample even
    though gamma/delta belong to other pairs."""
    nodes = [
        _ai_session(conv_id="c1", project="alpha"),
        _ai_session(conv_id="c1", project="beta"),
        _ai_session(conv_id="c1", project="gamma"),
        _ai_session(conv_id="c1", project="delta"),
    ]
    rg = build_project_relationships(_graph(nodes))
    for rel in rg.relationships:
        pair = {rel.project_a, rel.project_b}
        for sample in rel.sample_evidence_node_ids:
            suffix_project = sample.rsplit(":", 1)[-1]
            assert suffix_project in pair, (
                f"pair {pair} got off-pair sample {sample!r} "
                f"(project {suffix_project})"
            )


def test_render_includes_top_pairs_and_summary():
    nodes = [
        _ai_session(conv_id="c1", project="alpha"),
        _ai_session(conv_id="c1", project="beta"),
    ]
    rg = build_project_relationships(_graph(nodes))
    rendered = render_project_relationships(rg)
    assert "alpha" in rendered
    assert "beta" in rendered
    assert "1 edges" in rendered
    assert "ai_sessions" in rendered


def test_render_empty_graph_yields_no_edges_message():
    rg = build_project_relationships(_graph([]))
    rendered = render_project_relationships(rg)
    assert "No cross-project edges" in rendered
