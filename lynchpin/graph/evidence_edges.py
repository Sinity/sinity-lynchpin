"""Evidence graph edge builders."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Sequence

from ..core.parse import as_local
from ..core.evidence_graph import EvidenceEdge, EvidenceNode

TOOL_COMMAND_TOKENS: dict[str, tuple[str, ...]] = {
    "Bash": ("pytest", "cargo", "just", "npm", "git", "make", "nix", "ruff", "mypy"),
    "Edit": (),
    "Read": (),
    "Write": (),
}


def same_project_day_edges(nodes: Sequence[EvidenceNode]) -> tuple[EvidenceEdge, ...]:
    """Emit one cross-source edge per distinct (date, project, sourceA, sourceB) pair.

    Previous behavior chained all nodes in a (date, project) bucket, emitting N-1
    consecutive edges. ~97% of these were same-source pairs (git↔git, github↔github),
    adding no cross-evidence value while inflating edge count by ~90%.

    Fix: take one representative node per source per bucket, and emit edges only
    between distinct source pairs.
    """
    grouped: dict[tuple[date, str], dict[str, EvidenceNode]] = defaultdict(lambda: {})
    for node in nodes:
        if node.kind in {"analysis_artifact", "analysis_claim"}:
            continue
        if node.project:
            by_source = grouped[(node.date, node.project)]
            # Keep the first node per source (deterministic by id order)
            if node.source not in by_source:
                by_source[node.source] = node

    edges: list[EvidenceEdge] = []
    for (day, project), by_source in grouped.items():
        if len(by_source) < 2:
            continue
        sources = sorted(by_source.keys())
        for i, sourceA in enumerate(sources):
            for sourceB in sources[i + 1:]:
                nodeA = by_source[sourceA]
                nodeB = by_source[sourceB]
                edges.append(
                    EvidenceEdge(
                        nodeA.id, nodeB.id, "same_project_day",
                        f"{project} on {day} ({sourceA}↔{sourceB})", 0.4
                    )
                )
    return tuple(edges)


def temporal_overlap_edges(nodes: Sequence[EvidenceNode]) -> tuple[EvidenceEdge, ...]:
    """Emit cross-source overlap edges within each project.

    Treats point events (start == end, e.g. commits whose authored_at is the
    only timestamp) as instants that overlap an interval iff the instant
    falls inside it.

    Two intervals overlap iff max(starts) < min(ends). A point inside an
    interval is the degenerate case. Same-source pairs are skipped to avoid
    redundant edges (the within-source temporal grouping is captured
    elsewhere by temporal_proximity_edges).
    """
    grouped: dict[str, list[EvidenceNode]] = defaultdict(list)
    for node in nodes:
        if (
            node.project
            and node.start is not None
            and node.end is not None
            and node.end >= node.start
        ):
            grouped[node.project].append(node)
    edges: list[EvidenceEdge] = []
    for group in grouped.values():
        timed = sorted(group, key=node_time_sort_key)
        for idx, left in enumerate(timed):
            if left.start is None or left.end is None:
                continue
            left_start = as_local(left.start)
            left_end = as_local(left.end)
            for right in timed[idx + 1 :]:
                if right.start is None or right.end is None:
                    continue
                right_start = as_local(right.start)
                right_end = as_local(right.end)
                # Sorted by start, so right_start >= left_start. Past
                # left_end => no further right will overlap.
                if right_start > left_end:
                    break
                if left.source == right.source:
                    continue
                # Half-open interval semantics for interval-vs-interval
                # (consecutive sessions touching at boundary don't overlap).
                # For point events (commit with start==end), require the
                # point to fall inside the other's [start, end] range.
                left_is_point = left_start == left_end
                right_is_point = right_start == right_end
                if left_is_point and right_is_point:
                    overlap = left_start == right_start
                elif left_is_point:
                    overlap = right_start <= left_start <= right_end
                elif right_is_point:
                    overlap = left_start <= right_start <= left_end
                else:
                    overlap = max(left_start, right_start) < min(left_end, right_end)
                if overlap:
                    edges.append(
                        EvidenceEdge(
                            left.id,
                            right.id,
                            "temporal_overlap",
                            f"{left.source} overlaps {right.source}",
                            0.7,
                        )
                    )
    return tuple(edges)


def mentions_project_edges(nodes: Sequence[EvidenceNode]) -> tuple[EvidenceEdge, ...]:
    """Emit mentions_project edges from raw-log nodes.

    Extracts project mentions from node summaries/payloads and emits edges to
    representative commits on the same day within the same project.

    Weight: 0.6 (between same_project_day=0.4 and references=0.9).
    """
    from ..core.project_mentions import projects_mentioned_in_text

    # Collect all commits by (date, project)
    commits_by_day_project: dict[tuple[date, str], list[EvidenceNode]] = defaultdict(list)
    for node in nodes:
        if node.kind == "commit" and node.project:
            commits_by_day_project[(node.date, node.project)].append(node)

    edges: list[EvidenceEdge] = []
    for node in nodes:
        if node.kind != "raw_log":
            continue
        if node.project:
            # Skip nodes that already have a project attribution
            continue

        # Extract text to search for mentions
        text = node.summary or ""
        if node.payload:
            text += " " + str(node.payload.get("body") or "")

        mentioned = projects_mentioned_in_text(text)
        if not mentioned:
            continue

        # For each mentioned project, emit edge to a representative commit on same day
        for project in mentioned:
            candidates = commits_by_day_project.get((node.date, project), [])
            if not candidates:
                continue
            # Use most recent commit (sorted by id as tiebreaker)
            target = sorted(candidates, key=lambda n: (n.start or n.date, n.id))[-1]
            edges.append(
                EvidenceEdge(
                    node.id,
                    target.id,
                    "mentions_project",
                    f"{node.source} mentions {project}",
                    weight=0.6,
                )
            )

    return tuple(edges)


def temporal_proximity_edges(
    nodes: Sequence[EvidenceNode], *, max_gap_min: int = 90
) -> tuple[EvidenceEdge, ...]:
    grouped: dict[tuple[date, str], list[EvidenceNode]] = defaultdict(list)
    for node in nodes:
        if (
            node.kind in {"analysis_artifact", "analysis_claim"}
            or node.project is None
            or node.start is None
        ):
            continue
        grouped[(node.date, node.project)].append(node)

    edges: list[EvidenceEdge] = []
    max_gap_s = max_gap_min * 60
    for group in grouped.values():
        timed = sorted(group, key=node_time_sort_key)
        for idx, left in enumerate(timed):
            left_at = node_anchor_time(left)
            if left_at is None:
                continue
            for right in timed[idx + 1 :]:
                right_at = node_anchor_time(right)
                if right_at is None:
                    continue
                gap_s = abs((right_at - left_at).total_seconds())
                if gap_s > max_gap_s:
                    break
                if left.source == right.source:
                    continue
                if (
                    left.start is not None
                    and right.start is not None
                    and left.end is not None
                    and right.end is not None
                    and as_local(left.end) > as_local(right.start)
                    and as_local(right.end) > as_local(left.start)
                ):
                    continue
                gap_min = round(gap_s / 60)
                edges.append(
                    EvidenceEdge(
                        left.id,
                        right.id,
                        "temporal_proximity",
                        f"{left.source} within {gap_min}m of {right.source}",
                        proximity_weight(gap_min),
                    )
                )
    return tuple(edges)


def cross_boundary_edges(
    boundary_nodes: Sequence[EvidenceNode],
    tail_nodes: Sequence[EvidenceNode],
) -> tuple[EvidenceEdge, ...]:
    """Build only predecessor↔tail edges for an incremental graph.

    The tail graph already contains every tail-internal edge.  Run each
    indexed/grouped builder once over the bounded boundary+tail set, then keep
    only edges crossing the partition.  Invoking every builder separately for
    every boundary×tail pair turns a modest bounded window into millions of
    Python calls and is substantially slower than the builders' own grouping.
    """
    builders = (
        same_project_day_edges,
        temporal_overlap_edges,
        temporal_proximity_edges,
        mentions_project_edges,
    )
    boundary_ids = {node.id for node in boundary_nodes}
    tail_ids = {node.id for node in tail_nodes}
    combined = tuple(boundary_nodes) + tuple(tail_nodes)
    edges: list[EvidenceEdge] = []
    for builder in builders:
        edges.extend(
            edge
            for edge in builder(combined)
            if (
                edge.source_id in boundary_ids
                and edge.target_id in tail_ids
            )
            or (
                edge.source_id in tail_ids
                and edge.target_id in boundary_ids
            )
        )
    unique: dict[tuple[str, str, str], EvidenceEdge] = {}
    for edge in edges:
        unique[(edge.source_id, edge.target_id, edge.relation)] = edge
    return tuple(unique.values())


def node_time_sort_key(node: EvidenceNode) -> tuple[str, str, str]:
    anchor = node_anchor_time(node)
    return (anchor.isoformat() if anchor is not None else "", node.source, node.id)


def node_anchor_time(node: EvidenceNode) -> datetime | None:
    if node.start is None:
        return None
    return as_local(node.start)


def proximity_weight(gap_min: int) -> float:
    if gap_min <= 15:
        return 0.82
    if gap_min <= 60:
        return 0.7
    return 0.58


__all__ = [
    "cross_boundary_edges",
    "mentions_project_edges",
    "same_project_day_edges",
    "temporal_overlap_edges",
    "temporal_proximity_edges",
]
