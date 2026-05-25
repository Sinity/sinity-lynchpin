"""Range-scoped evidence graph for current-state and narrative analysis."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Sequence

from ..core.evidence import CostClass
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

    Holds a cache of base evidence graphs keyed by ``(start, end,
    projects)`` so that ``project_velocity_windows`` and
    ``current_state_context`` can share work without going through a
    global cache. Opt-in: callers must thread the same ``RefreshContext``
    through both consumers; otherwise behavior is identical to the
    pre-7E path.
    """

    _cache: dict[tuple[date, date, bool, tuple[str, ...]], "EvidenceGraph"] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self._cache is None:
            object.__setattr__(self, "_cache", {})

    def base_graph(
        self,
        *,
        start: date,
        end: date,
        projects: Sequence[str] | None = None,
        include_github_frontier: bool = False,
    ) -> "EvidenceGraph":
        key = (start, end, include_github_frontier, tuple(sorted(projects)) if projects else ())
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        graph = build_base_evidence_graph(
            start=start,
            end=end,
            projects=projects,
            include_github_frontier=include_github_frontier,
        )
        self._cache[key] = graph
        return graph


def build_base_evidence_graph(
    *,
    start: date,
    end: date,
    projects: Sequence[str] | None = None,
    include_github_frontier: bool = False,
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

    mode: CostClass = "network" if include_github_frontier else "materialized"
    log.info("evidence_graph: loading base sources start=%s end=%s github_frontier=%s", start, end, include_github_frontier)
    evidence_sources.add_base_source_nodes(
        nodes,
        edges,
        start=start,
        end=end,
        selected=selected,
        mode=mode,
        include_spotify=True,
    )
    log.info("evidence_graph: loaded base sources nodes=%d edges=%d", len(nodes), len(edges))

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
    include_github_frontier: bool = False,
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
    mode: CostClass
    if refresh_context is not None:
        log.info("evidence_graph: loading base graph from refresh context start=%s end=%s github_frontier=%s", start, end, include_github_frontier)
        base = refresh_context.base_graph(
            start=start,
            end=end,
            projects=projects,
            include_github_frontier=include_github_frontier,
        )
        nodes = list(base.nodes)
        edges = list(base.edges)
        mode = base.mode
    else:
        nodes = []
        edges = []
        mode = "network" if include_github_frontier else "materialized"
        log.info("evidence_graph: loading base sources start=%s end=%s github_frontier=%s", start, end, include_github_frontier)
        evidence_sources.add_base_source_nodes(
            nodes,
            edges,
            start=start,
            end=end,
            selected=selected,
            mode=mode,
            include_spotify=False,
        )
    log.info("evidence_graph: base graph nodes=%d edges=%d", len(nodes), len(edges))

    log.info("evidence_graph: adding machine analysis nodes")
    add_machine_analysis_nodes(
        nodes,
        edges,
        start=start,
        end=end,
        selected=selected,
        exclude_names=frozenset(exclude_analysis_artifacts),
    )
    log.info("evidence_graph: after machine analysis nodes=%d edges=%d", len(nodes), len(edges))
    now = datetime.now().astimezone()
    log.info("evidence_graph: adding analysis artifacts")
    evidence_analysis.add_analysis_artifacts(
        nodes,
        edges,
        end=end,
        selected=selected,
        exclude_names=frozenset(exclude_analysis_artifacts),
    )
    log.info("evidence_graph: adding analysis claims")
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
    if len(nodes) > 100_000:
        log.warning(
            "evidence_graph: large graph build nodes=%d; relation builders run in bounded source groups",
            len(nodes),
        )
    log.info("evidence_graph: deriving same-project/day edges for %d nodes", len(nodes))
    same_project_edges = tuple(
        edge
        for edge in evidence_edges.same_project_day_edges(nodes)
        if edge.source_id in node_ids and edge.target_id in node_ids
    )
    edges.extend(same_project_edges)
    log.info("evidence_graph: added %d same-project/day edges", len(same_project_edges))
    log.info("evidence_graph: deriving temporal-overlap edges")
    overlap_edges = tuple(
        edge
        for edge in evidence_edges.temporal_overlap_edges(nodes)
        if edge.source_id in node_ids and edge.target_id in node_ids
    )
    edges.extend(overlap_edges)
    log.info("evidence_graph: added %d temporal-overlap edges", len(overlap_edges))
    log.info("evidence_graph: deriving temporal-proximity edges")
    proximity_edges = tuple(
        edge
        for edge in evidence_edges.temporal_proximity_edges(nodes)
        if edge.source_id in node_ids and edge.target_id in node_ids
    )
    edges.extend(proximity_edges)
    log.info("evidence_graph: added %d temporal-proximity edges", len(proximity_edges))
    log.info("evidence_graph: deriving file/symbol overlap edges through substrate")
    overlap_refresh_id = f"overlap:{generated_at.isoformat()}"
    sql_edges = evidence_edges.overlap_edges_via_substrate(
        nodes, refresh_id=overlap_refresh_id
    )
    edges.extend(
        edge
        for edge in sql_edges
        if edge.source_id in node_ids and edge.target_id in node_ids
    )
    log.info("evidence_graph: added %d file/symbol overlap edges", len(sql_edges))
    log.info("evidence_graph: deriving tool-overlap edges")
    edges.extend(
        edge
        for edge in evidence_edges.polylogue_work_event_tool_overlap_edges(nodes)
        if edge.source_id in node_ids and edge.target_id in node_ids
    )
    log.info("evidence_graph: deriving mentions-project edges")
    mentions_edges = tuple(
        edge
        for edge in evidence_edges.mentions_project_edges(nodes)
        if edge.source_id in node_ids and edge.target_id in node_ids
    )
    edges.extend(mentions_edges)
    log.info("evidence_graph: added %d mentions-project edges", len(mentions_edges))

    log.info("evidence_graph: checking source readiness")
    readiness = source_readiness(
        start=start,
        end=end,
        include_polylogue_product_counts=True,
        include_github_frontier=mode == "network",
        include_analysis_inventory=True,
    )
    caveats = tuple(readiness.caveats)
    deduped_nodes = _dedupe_nodes(nodes)
    node_ids = {node.id for node in deduped_nodes}
    deduped_edges = tuple(
        edge
        for edge in _dedupe_edges(edges)
        if edge.source_id in node_ids and edge.target_id in node_ids
    )
    log.info("evidence_graph: finalized nodes=%d edges=%d", len(deduped_nodes), len(deduped_edges))
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
    return f"graph:{graph.start.isoformat()}:{graph.end.isoformat()}:all"


def promote_graph_to_substrate(
    graph: "EvidenceGraph",
    *,
    refresh_id: str,
    projects: Sequence[str] = (),
) -> None:
    """Best-effort write of graph to DuckDB substrate. Errors logged, not raised."""
    try:
        from lynchpin.substrate import connect, apply_schema
        from lynchpin.substrate.claims import promote_analysis_claims
        from lynchpin.substrate.graph import promote_evidence_graph
        from lynchpin.analysis.active.substrate_promote_graph import _analysis_claim_rows
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
            counts["analysis_claims"] = promote_analysis_claims(
                conn,
                refresh_id=refresh_id,
                claims=_analysis_claim_rows(graph),
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
