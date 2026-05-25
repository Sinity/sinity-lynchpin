"""Generic evidence-graph walker.

The graph composer (`build_evidence_graph`) and the substrate loader
(`load_evidence_graph`) produce an in-memory `EvidenceGraph` with typed
nodes and edges. There are several special-purpose walkers
(`detect_closure_chains`, `build_project_relationships`), but no generic
BFS that agents can drive from a starting node with edge-kind filters
and a depth cap. That gap forces consumers into raw SQL against
`evidence_node`/`evidence_edge` for every "what's connected to X?"
question.

This module is that walker. Pure Python over the already-loaded graph,
cycle-safe, bidirectional, hard-capped by depth and node budget.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from lynchpin.core.evidence_graph import (
    EvidenceEdge,
    EvidenceGraph,
    EvidenceNode,
    EvidenceRelation,
)

Direction = Literal["out", "in", "both"]


@dataclass(frozen=True)
class WalkStep:
    """One visited node with the edge that reached it.

    `parent_edge` is None for the start node. For subsequent steps,
    `parent_edge.source_id` is the node we walked from (when direction
    is "out") or the node we walked to (when direction is "in"); the
    walker tracks both so callers can reconstruct the path either way.
    """

    node: EvidenceNode
    depth: int
    parent_edge: EvidenceEdge | None
    direction_followed: Direction | None  # None for start node


@dataclass(frozen=True)
class WalkResult:
    """Output of a single `walk_evidence` invocation.

    `truncated` is True iff the walk hit the depth or node cap; `reason`
    names which cap fired. Edges returned are those traversed at least
    once, deduplicated.
    """

    start_id: str
    direction: Direction
    edge_kinds: tuple[EvidenceRelation, ...] | None
    max_depth: int
    max_nodes: int
    steps: tuple[WalkStep, ...]
    edges: tuple[EvidenceEdge, ...]
    truncated: bool
    reason: str | None


_HARD_DEPTH_CAP = 5
_HARD_NODE_CAP = 1000


def walk_evidence(
    graph: EvidenceGraph,
    start_id: str,
    *,
    edge_kinds: Sequence[EvidenceRelation] | None = None,
    max_depth: int = 3,
    max_nodes: int = 200,
    direction: Direction = "out",
    node_predicate: Callable[[EvidenceNode], bool] | None = None,
) -> WalkResult:
    """Breadth-first walk over the evidence graph from `start_id`.

    Caps are clamped: `max_depth` to `_HARD_DEPTH_CAP` (5), `max_nodes`
    to `_HARD_NODE_CAP` (1000). Cycle-safe via visited set.

    Direction semantics:
        - "out":  follow source_id == current → target_id
        - "in":   follow target_id == current → source_id
        - "both": follow either; useful for "what's adjacent to X?"

    `edge_kinds`, when given, filters edges by `relation` at every step.
    `node_predicate`, when given, filters which nodes are kept in the
    output (but the walk still traverses through them — predicate is for
    output, not pruning).
    """
    max_depth = min(max_depth, _HARD_DEPTH_CAP)
    max_nodes = min(max_nodes, _HARD_NODE_CAP)
    allowed_kinds = set(edge_kinds) if edge_kinds is not None else None

    node_index = graph.node_map()
    if start_id not in node_index:
        return WalkResult(
            start_id=start_id,
            direction=direction,
            edge_kinds=tuple(edge_kinds) if edge_kinds else None,
            max_depth=max_depth,
            max_nodes=max_nodes,
            steps=(),
            edges=(),
            truncated=False,
            reason=f"start_id {start_id!r} not in graph",
        )

    out_adj, in_adj = _build_adjacency(graph.edges, allowed_kinds)

    steps: list[WalkStep] = [
        WalkStep(node=node_index[start_id], depth=0, parent_edge=None, direction_followed=None)
    ]
    traversed_edges: list[EvidenceEdge] = []
    seen_edge_keys: set[tuple[str, str, str]] = set()
    visited: set[str] = {start_id}
    queue: deque[tuple[str, int]] = deque([(start_id, 0)])

    truncated = False
    reason: str | None = None

    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            continue

        neighbors: list[tuple[EvidenceEdge, str, Direction]] = []
        if direction in ("out", "both"):
            for edge in out_adj.get(current_id, ()):
                neighbors.append((edge, edge.target_id, "out"))
        if direction in ("in", "both"):
            for edge in in_adj.get(current_id, ()):
                neighbors.append((edge, edge.source_id, "in"))

        for edge, neighbor_id, walked_dir in neighbors:
            edge_key = (edge.source_id, edge.target_id, edge.relation)
            if edge_key not in seen_edge_keys:
                seen_edge_keys.add(edge_key)
                traversed_edges.append(edge)

            if neighbor_id in visited:
                continue
            if neighbor_id not in node_index:
                continue

            visited.add(neighbor_id)
            steps.append(
                WalkStep(
                    node=node_index[neighbor_id],
                    depth=depth + 1,
                    parent_edge=edge,
                    direction_followed=walked_dir,
                )
            )
            if len(visited) >= max_nodes:
                truncated = True
                reason = f"max_nodes cap ({max_nodes}) reached"
                break
            queue.append((neighbor_id, depth + 1))

        if truncated:
            break

    if not truncated and any(step.depth == max_depth for step in steps):
        # We surfaced nodes AT the depth cap but didn't recurse from them;
        # callers may want to know there's more reachable graph beyond.
        truncated = True
        reason = f"max_depth cap ({max_depth}) reached"

    if node_predicate is not None:
        steps = [step for step in steps if step.depth == 0 or node_predicate(step.node)]

    return WalkResult(
        start_id=start_id,
        direction=direction,
        edge_kinds=tuple(edge_kinds) if edge_kinds else None,
        max_depth=max_depth,
        max_nodes=max_nodes,
        steps=tuple(steps),
        edges=tuple(traversed_edges),
        truncated=truncated,
        reason=reason,
    )


def _build_adjacency(
    edges: Iterable[EvidenceEdge],
    allowed_kinds: set[EvidenceRelation] | None,
) -> tuple[dict[str, list[EvidenceEdge]], dict[str, list[EvidenceEdge]]]:
    out_adj: dict[str, list[EvidenceEdge]] = defaultdict(list)
    in_adj: dict[str, list[EvidenceEdge]] = defaultdict(list)
    for edge in edges:
        if allowed_kinds is not None and edge.relation not in allowed_kinds:
            continue
        out_adj[edge.source_id].append(edge)
        in_adj[edge.target_id].append(edge)
    return out_adj, in_adj


__all__ = ["Direction", "WalkResult", "WalkStep", "walk_evidence"]
