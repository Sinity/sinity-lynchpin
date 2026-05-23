"""Range-scoped evidence graph for current-state and narrative analysis."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Sequence

from ..core.evidence import CostClass, EvidenceCaveat
from ..core.evidence_graph import (
    EvidenceEdge,
    EvidenceGraph,
    EvidenceNode,
)
from . import evidence_analysis, evidence_edges, evidence_sources
from .evidence_projects import selected_projects
from .machine_analysis import add_machine_analysis_nodes
from .source_readiness import source_readiness

log = logging.getLogger(__name__)


@dataclass
class RefreshContext:
    """Per-refresh memoization for graph construction.

    Holds a cache of base evidence graphs keyed by ``(start, end, mode,
    projects)`` so that ``project_velocity_windows`` and
    ``current_state_context`` can share work without going through a
    global cache. Opt-in: callers must thread the same ``RefreshContext``
    through both consumers; otherwise behavior is identical to the
    pre-7E path.
    """

    _cache: dict[tuple[date, date, str, tuple[str, ...]], "EvidenceGraph"] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self._cache is None:
            object.__setattr__(self, "_cache", {})

    def base_graph(
        self,
        *,
        start: date,
        end: date,
        projects: Sequence[str] | None = None,
        mode: CostClass = "local-fast",
    ) -> "EvidenceGraph":
        key = (start, end, mode, tuple(sorted(projects)) if projects else ())
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        graph = build_base_evidence_graph(
            start=start, end=end, projects=projects, mode=mode
        )
        self._cache[key] = graph
        return graph


def build_base_evidence_graph(
    *,
    start: date,
    end: date,
    projects: Sequence[str] | None = None,
    mode: CostClass = "local-fast",
    promote: bool = False,
    promote_refresh_id: str | None = None,
    promote_projects: Sequence[str] = (),
) -> EvidenceGraph:
    """Build the base evidence graph: every source except generated analysis
    artifacts and claims.

    Used by callers — like ``project_velocity_windows._correlation_rows`` —
    that must not see the analysis overlay they are about to write.
    """
    selected = selected_projects(projects)
    nodes: list[EvidenceNode] = []
    edges: list[EvidenceEdge] = []
    now = datetime.now().astimezone()

    evidence_sources.add_base_source_nodes(
        nodes,
        edges,
        start=start,
        end=end,
        selected=selected,
        mode=mode,
        include_spotify=True,
    )

    return _finalize_graph(
        nodes=nodes,
        edges=edges,
        start=start,
        end=end,
        mode=mode,
        generated_at=now,
        promote=promote,
        promote_refresh_id=promote_refresh_id,
        promote_projects=promote_projects,
    )


def build_evidence_graph(
    *,
    start: date,
    end: date,
    projects: Sequence[str] | None = None,
    mode: CostClass = "local-fast",
    exclude_analysis_artifacts: Sequence[str] = (),
    refresh_context: RefreshContext | None = None,
    promote: bool = False,
    promote_refresh_id: str | None = None,
    promote_projects: Sequence[str] = (),
) -> EvidenceGraph:
    """Build a local evidence graph for a date range.

    If ``refresh_context`` is supplied, the base layer is reused from the
    context's cache; otherwise the base is built fresh.
    """
    selected = selected_projects(projects)
    if refresh_context is not None:
        base = refresh_context.base_graph(
            start=start, end=end, projects=projects, mode=mode
        )
        nodes = list(base.nodes)
        edges = list(base.edges)
    else:
        nodes = []
        edges = []
        evidence_sources.add_base_source_nodes(
            nodes,
            edges,
            start=start,
            end=end,
            selected=selected,
            mode=mode,
            include_spotify=False,
        )

    add_machine_analysis_nodes(
        nodes,
        edges,
        start=start,
        end=end,
        selected=selected,
        exclude_names=frozenset(exclude_analysis_artifacts),
    )
    now = datetime.now().astimezone()
    if mode != "local-fast":
        evidence_analysis.add_analysis_artifacts(
            nodes,
            edges,
            end=end,
            selected=selected,
            exclude_names=frozenset(exclude_analysis_artifacts),
        )
        evidence_analysis.add_analysis_claims(
            nodes,
            edges,
            end=end,
            selected=selected,
            exclude_names=frozenset(exclude_analysis_artifacts),
        )

    return _finalize_graph(
        nodes=nodes,
        edges=edges,
        start=start,
        end=end,
        mode=mode,
        generated_at=now,
        promote=promote,
        promote_refresh_id=promote_refresh_id,
        promote_projects=promote_projects,
    )


def _finalize_graph(
    *,
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    start: date,
    end: date,
    mode: CostClass,
    generated_at: datetime,
    promote: bool = False,
    promote_refresh_id: str | None = None,
    promote_projects: Sequence[str] = (),
) -> EvidenceGraph:
    node_ids = {node.id for node in nodes}
    edges.extend(
        edge
        for edge in evidence_edges.same_project_day_edges(nodes)
        if edge.source_id in node_ids and edge.target_id in node_ids
    )
    edges.extend(
        edge
        for edge in evidence_edges.temporal_overlap_edges(nodes)
        if edge.source_id in node_ids and edge.target_id in node_ids
    )
    edges.extend(
        edge
        for edge in evidence_edges.temporal_proximity_edges(nodes)
        if edge.source_id in node_ids and edge.target_id in node_ids
    )
    if mode != "local-fast":
        overlap_refresh_id = f"overlap:{generated_at.isoformat()}"
        sql_edges = evidence_edges.overlap_edges_via_substrate(
            nodes, refresh_id=overlap_refresh_id
        )
        edges.extend(
            edge
            for edge in sql_edges
            if edge.source_id in node_ids and edge.target_id in node_ids
        )
        edges.extend(
            edge
            for edge in evidence_edges.polylogue_work_event_tool_overlap_edges(nodes)
            if edge.source_id in node_ids and edge.target_id in node_ids
        )

    readiness = source_readiness(
        start=start,
        end=end,
        include_heavy_counts=mode != "local-fast",
        include_github_frontier=mode == "network",
        include_analysis_inventory=mode != "local-fast",
    )
    caveats = tuple(readiness.caveats)
    if mode == "local-fast":
        caveats += (
            EvidenceCaveat(
                "evidence_graph",
                "partial",
                "local-fast graph uses daily focus aggregates and commit-referenced GitHub refs only.",
            ),
        )
        caveats += (
            EvidenceCaveat(
                "evidence_graph",
                "partial",
                "local-fast omits heavyweight analysis overlays, temporal-signal detection, readiness forecasting, Spotify scans, and Polylogue work-event detail unless a materialized substrate graph is loaded.",
            ),
        )
    deduped_nodes = _dedupe_nodes(nodes)
    node_ids = {node.id for node in deduped_nodes}
    deduped_edges = tuple(
        edge
        for edge in _dedupe_edges(edges)
        if edge.source_id in node_ids and edge.target_id in node_ids
    )
    graph = EvidenceGraph(
        start=start,
        end=end,
        generated_at=generated_at,
        mode=mode,
        nodes=tuple(
            sorted(
                deduped_nodes,
                key=lambda node: (node.date, node.project or "", node.source, node.id),
            )
        ),
        edges=deduped_edges,
        caveats=caveats,
    )

    if promote:
        refresh_id = promote_refresh_id or _default_graph_refresh_id(graph)
        promote_graph_to_substrate(
            graph,
            refresh_id=refresh_id,
            projects=promote_projects,
        )

    return graph


def _default_graph_refresh_id(graph: "EvidenceGraph") -> str:
    return f"graph:{graph.start.isoformat()}:{graph.end.isoformat()}:{graph.mode}:all"


def promote_graph_to_substrate(
    graph: "EvidenceGraph",
    *,
    refresh_id: str,
    projects: Sequence[str] = (),
) -> None:
    """Best-effort write of graph to DuckDB substrate. Errors logged, not raised."""
    try:
        from lynchpin.substrate import connect, apply_schema
        from lynchpin.substrate.graph import promote_evidence_graph
    except ImportError as exc:
        log.warning("DuckDB substrate unavailable: %s", exc)
        return
    try:
        with connect() as conn:
            apply_schema(conn)
            counts = promote_evidence_graph(
                conn,
                refresh_id=refresh_id,
                graph=graph,
                projects=projects,
            )
            log.info(
                "Promoted evidence graph to substrate: %s (refresh_id=%s)",
                counts,
                refresh_id,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort write, never crash refresh
        log.warning("Failed to promote evidence graph to substrate: %s", exc)


def _dedupe_nodes(nodes: Sequence[EvidenceNode]) -> tuple[EvidenceNode, ...]:
    by_id: dict[str, EvidenceNode] = {}
    for node in nodes:
        by_id[node.id] = node
    return tuple(by_id.values())


def _dedupe_edges(edges: Sequence[EvidenceEdge]) -> tuple[EvidenceEdge, ...]:
    by_key: dict[tuple[str, str, str], EvidenceEdge] = {}
    for edge in edges:
        left, right = sorted((edge.source_id, edge.target_id))
        by_key[(left, right, edge.relation)] = edge
    return tuple(by_key.values())


__all__ = [
    "build_evidence_graph",
    "promote_graph_to_substrate",
]
