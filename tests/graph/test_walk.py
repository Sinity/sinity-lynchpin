"""Tests for the generic evidence-graph walker.

Builds small synthetic graphs to verify BFS shape, cycle handling,
edge-kind filtering, depth/node caps, bidirectional traversal, and
node-predicate filtering of output.
"""

from __future__ import annotations

from datetime import date, datetime

from lynchpin.core.evidence_graph import (
    EvidenceEdge,
    EvidenceGraph,
    EvidenceNode,
)
from lynchpin.graph.walk import walk_evidence


def _node(node_id: str, project: str | None = "p", kind: str = "commit") -> EvidenceNode:
    return EvidenceNode(
        id=node_id,
        kind=kind,  # type: ignore[arg-type]
        source="test",
        date=date(2026, 5, 25),
        project=project,
        summary=node_id,
    )


def _edge(source: str, target: str, relation: str = "references") -> EvidenceEdge:
    return EvidenceEdge(
        source_id=source,
        target_id=target,
        relation=relation,  # type: ignore[arg-type]
        evidence=f"{source}->{target}",
    )


def _graph(nodes: list[EvidenceNode], edges: list[EvidenceEdge]) -> EvidenceGraph:
    return EvidenceGraph(
        start=date(2026, 5, 1),
        end=date(2026, 5, 31),
        generated_at=datetime(2026, 5, 25, 12, 0, 0),
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


def test_walk_linear_chain_reaches_all_at_depth() -> None:
    nodes = [_node(c) for c in "abcd"]
    edges = [_edge("a", "b"), _edge("b", "c"), _edge("c", "d")]
    result = walk_evidence(_graph(nodes, edges), "a", max_depth=3)
    assert [s.node.id for s in result.steps] == ["a", "b", "c", "d"]
    assert [s.depth for s in result.steps] == [0, 1, 2, 3]
    assert len(result.edges) == 3


def test_walk_respects_depth_cap_and_marks_truncated() -> None:
    nodes = [_node(c) for c in "abcd"]
    edges = [_edge("a", "b"), _edge("b", "c"), _edge("c", "d")]
    result = walk_evidence(_graph(nodes, edges), "a", max_depth=2)
    ids = [s.node.id for s in result.steps]
    assert ids == ["a", "b", "c"]
    assert "d" not in ids
    assert result.truncated is True
    assert result.reason is not None
    assert "max_depth" in result.reason


def test_walk_handles_cycle_without_infinite_loop() -> None:
    nodes = [_node(c) for c in "abc"]
    edges = [_edge("a", "b"), _edge("b", "c"), _edge("c", "a")]
    result = walk_evidence(_graph(nodes, edges), "a", max_depth=5)
    visited_ids = {s.node.id for s in result.steps}
    assert visited_ids == {"a", "b", "c"}


def test_walk_filters_by_edge_kind() -> None:
    nodes = [_node(c) for c in "abcd"]
    edges = [
        _edge("a", "b", "references"),
        _edge("a", "c", "temporal_overlap"),
        _edge("c", "d", "references"),
    ]
    result = walk_evidence(_graph(nodes, edges), "a", edge_kinds=["references"])
    visited_ids = [s.node.id for s in result.steps]
    assert visited_ids == ["a", "b"]
    assert all(e.relation == "references" for e in result.edges)


def test_walk_max_nodes_cap_short_circuits() -> None:
    nodes = [_node(f"n{i}") for i in range(20)]
    # Star graph: n0 points at all others.
    edges = [_edge("n0", f"n{i}") for i in range(1, 20)]
    result = walk_evidence(_graph(nodes, edges), "n0", max_nodes=5)
    assert len(result.steps) == 5
    assert result.truncated is True
    assert "max_nodes" in (result.reason or "")


def test_walk_direction_in_follows_reverse_edges() -> None:
    nodes = [_node(c) for c in "abcd"]
    # a -> b -> c, and d -> c (separate inbound to c)
    edges = [_edge("a", "b"), _edge("b", "c"), _edge("d", "c")]
    result = walk_evidence(_graph(nodes, edges), "c", direction="in", max_depth=3)
    visited_ids = {s.node.id for s in result.steps}
    # From c walking IN we reach b, then a; we also reach d (separate inbound)
    assert visited_ids == {"a", "b", "c", "d"}


def test_walk_direction_both_traverses_either_way() -> None:
    nodes = [_node(c) for c in "abc"]
    edges = [_edge("a", "b"), _edge("c", "b")]
    result = walk_evidence(_graph(nodes, edges), "b", direction="both")
    visited_ids = {s.node.id for s in result.steps}
    assert visited_ids == {"a", "b", "c"}


def test_walk_with_unknown_start_id_returns_empty_with_reason() -> None:
    result = walk_evidence(_graph([_node("a")], []), "missing")
    assert result.steps == ()
    assert result.reason is not None
    assert "missing" in result.reason


def test_walk_node_predicate_filters_output_not_traversal() -> None:
    # Walk through a "transit" node b to reach c; ask for only commits.
    nodes = [
        _node("a", kind="commit"),
        _node("b", kind="github_ref"),
        _node("c", kind="commit"),
    ]
    edges = [_edge("a", "b"), _edge("b", "c")]
    result = walk_evidence(
        _graph(nodes, edges),
        "a",
        max_depth=3,
        node_predicate=lambda n: n.kind == "commit",
    )
    visited_ids = [s.node.id for s in result.steps]
    # 'a' is always kept (start node); 'b' filtered out; 'c' kept (commit)
    assert visited_ids == ["a", "c"]


def test_walk_hard_caps_clamp_user_provided_values() -> None:
    nodes = [_node(f"n{i}") for i in range(3)]
    edges = [_edge(f"n{i}", f"n{i + 1}") for i in range(2)]
    # max_depth=99 should clamp to _HARD_DEPTH_CAP=5
    result = walk_evidence(_graph(nodes, edges), "n0", max_depth=99, max_nodes=99999)
    assert result.max_depth == 5
    assert result.max_nodes == 1000


def test_walk_edges_deduplicated_under_both_direction() -> None:
    nodes = [_node(c) for c in "ab"]
    edges = [_edge("a", "b")]
    result = walk_evidence(_graph(nodes, edges), "a", direction="both", max_depth=3)
    # Edge a->b should be reported once even though "both" would visit it
    # in both adjacency maps.
    assert len(result.edges) == 1
