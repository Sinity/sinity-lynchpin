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


def _raw_log(*, ref: str, project: str) -> EvidenceNode:
    return EvidenceNode(
        id=f"raw-log:{ref}:{project}", kind="raw_log", source="raw_log",
        date=date(2026, 5, 7), project=project, summary="recorded work",
        payload={"source_path": f"{ref}.md", "line_no": 1},
    )


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
        _raw_log(ref="c1", project="alpha"),
        _raw_log(ref="c2", project="beta"),  # different notes
    ]
    rg = build_project_relationships(_graph(nodes))
    assert rg.relationships == ()


def test_sample_evidence_node_ids_stay_within_the_pair():
    """Sessions touching N>2 projects must not leak off-pair node ids into
    every pair's sample list. Previously a session touching {alpha, beta,
    gamma, delta} dumped all 4 node ids into the alpha-beta sample even
    though gamma/delta belong to other pairs."""
    nodes = [
        _raw_log(ref="c1", project="alpha"),
        _raw_log(ref="c1", project="beta"),
        _raw_log(ref="c1", project="gamma"),
        _raw_log(ref="c1", project="delta"),
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
        _raw_log(ref="c1", project="alpha"),
        _raw_log(ref="c1", project="beta"),
    ]
    rg = build_project_relationships(_graph(nodes))
    rendered = render_project_relationships(rg)
    assert "alpha" in rendered
    assert "beta" in rendered
    assert "1 edges" in rendered
    assert "raw_log" in rendered


def test_render_empty_graph_yields_no_edges_message():
    rg = build_project_relationships(_graph([]))
    rendered = render_project_relationships(rg)
    assert "No cross-project edges" in rendered
