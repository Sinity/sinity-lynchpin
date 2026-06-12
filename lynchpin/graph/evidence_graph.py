"""Range-scoped evidence graph for current-state and narrative analysis.

Evidence-graph BUILDER: assembles a range-scoped ``EvidenceGraph`` from sources
(git, polylogue, ActivityWatch, terminal, raw-log, clipboard, irc, GitHub,
analysis artifacts) and derives same-project/day, temporal-overlap,
proximity, and substrate-backed file/symbol edges. The data model (node/edge
dataclasses and kind literals) lives in ``lynchpin/core/evidence_graph.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Sequence

from ..core.evidence import CostClass
from ..core.evidence import EvidenceCaveat
from ..core.evidence import dedupe_caveats
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
class EvidenceGraphBuildContext:
    """Per-build memoization for graph construction.

    Holds a cache of base evidence graphs keyed by ``(start, end,
    projects)`` so that ``project_velocity_windows`` and
    ``current_state_context`` can share work without going through a
    global cache. Opt-in: callers must thread the same ``EvidenceGraphBuildContext``
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
        key = (
            start,
            end,
            include_github_frontier,
            tuple(sorted(projects)) if projects else (),
        )
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
    log.info(
        "evidence_graph: loading base sources start=%s end=%s github_frontier=%s",
        start,
        end,
        include_github_frontier,
    )
    source_caveats = evidence_sources.add_base_source_nodes(
        nodes,
        edges,
        start=start,
        end=end,
        selected=selected,
        mode=mode,
        include_spotify=True,
    )
    log.info(
        "evidence_graph: loaded base sources nodes=%d edges=%d", len(nodes), len(edges)
    )

    return _finalize_graph(
        nodes=nodes,
        edges=edges,
        start=start,
        end=end,
        mode=mode,
        generated_at=now,
        source_caveats=source_caveats,
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
    build_context: EvidenceGraphBuildContext | None = None,
    promote: bool = False,
    promote_refresh_id: str | None = None,
    promote_projects: Sequence[str] = (),
) -> EvidenceGraph:
    """Build a local evidence graph for a date range.

    If ``build_context`` is supplied, the base layer is reused from the
    context's cache; otherwise the base is built fresh.
    """
    selected = selected_projects(projects)
    mode: CostClass
    if build_context is not None:
        log.info(
            "evidence_graph: loading base graph from build context start=%s end=%s github_frontier=%s",
            start,
            end,
            include_github_frontier,
        )
        base = build_context.base_graph(
            start=start,
            end=end,
            projects=projects,
            include_github_frontier=include_github_frontier,
        )
        nodes = list(base.nodes)
        edges = list(base.edges)
        source_caveats = tuple(base.caveats)
        mode = base.mode
    else:
        nodes = []
        edges = []
        mode = "network" if include_github_frontier else "materialized"
        log.info(
            "evidence_graph: loading base sources start=%s end=%s github_frontier=%s",
            start,
            end,
            include_github_frontier,
        )
        source_caveats = evidence_sources.add_base_source_nodes(
            nodes,
            edges,
            start=start,
            end=end,
            selected=selected,
            mode=mode,
            include_spotify=False,
        )
    log.info("evidence_graph: base graph nodes=%d edges=%d", len(nodes), len(edges))

    now = datetime.now().astimezone()
    log.info("evidence_graph: adding analysis artifacts")
    analysis_artifacts = evidence_analysis.add_analysis_artifacts(
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
        artifacts=analysis_artifacts,
    )
    log.info("evidence_graph: adding machine analysis nodes")
    add_machine_analysis_nodes(
        nodes,
        edges,
        start=start,
        end=end,
        selected=selected,
        exclude_names=frozenset(exclude_analysis_artifacts),
    )
    log.info(
        "evidence_graph: after machine analysis nodes=%d edges=%d",
        len(nodes),
        len(edges),
    )

    return _finalize_graph(
        nodes=nodes,
        edges=edges,
        start=start,
        end=end,
        mode=mode,
        generated_at=now,
        source_caveats=source_caveats,
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
    source_caveats: tuple[EvidenceCaveat, ...] = (),
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
    log.info("evidence_graph: deriving file/symbol overlap edges")
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
        repair_materializations=False,
    )
    caveats = dedupe_caveats(tuple(readiness.caveats) + tuple(source_caveats))
    deduped_nodes = _dedupe_nodes(nodes)
    node_ids = {node.id for node in deduped_nodes}
    deduped_edges = tuple(
        edge
        for edge in _dedupe_edges(edges)
        if edge.source_id in node_ids and edge.target_id in node_ids
    )
    log.info(
        "evidence_graph: finalized nodes=%d edges=%d",
        len(deduped_nodes),
        len(deduped_edges),
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
                claims=analysis_claim_rows(graph),
            )
            log.info(
                "Promoted evidence graph to substrate: %s (refresh_id=%s)",
                counts,
                refresh_id,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort write, never crash refresh
        log.warning("Failed to promote evidence graph to substrate: %s", exc)


def analysis_claim_rows(graph: "EvidenceGraph") -> list[Any]:
    """Build substrate analysis-claim rows from an evidence graph.

    Lives in the graph layer because it operates purely over the graph and
    substrate-claim types; ``analysis.active.substrate_promote_graph`` imports
    it from here so the dependency direction stays analysis -> graph.
    """
    from lynchpin.graph.work_correlation import (
        supported_work_claims,
        work_day_correlations,
    )
    from lynchpin.substrate.claims import AnalysisClaimRow, claim_id

    rows = work_day_correlations(
        start=graph.start,
        end=graph.end,
        graph=graph,
    )
    claims = supported_work_claims(rows, graph=graph, limit=200)
    result: list[AnalysisClaimRow] = []
    for claim in claims:
        # Prefer real edge composite IDs (source_id->target_id:relation) so
        # load_claim_evidence can join back to evidence_edge rows. Fall back
        # to dimension labels only if strongest_edge_ids is unpopulated (e.g.
        # legacy callers not going through _work_claim).
        relation_ids = claim.strongest_edge_ids or tuple(
            _relation_id(value) for value in claim.strongest_relations
        )
        result.append(
            AnalysisClaimRow(
                claim_id=claim_id(
                    "supported_work", claim.date, claim.project, claim.summary
                ),
                claim_type="supported_work",
                project=claim.project,
                date=claim.date,
                support_level=claim.support_level,
                confidence=_confidence_for_support(claim.support_level),
                score=claim.score,
                summary=claim.summary,
                source_ids=(),
                relation_ids=relation_ids,
                caveats=claim.caveats,
                payload={
                    "sources": list(claim.sources),
                    "relation_count": claim.relation_count,
                    "strongest_relations": list(claim.strongest_relations),
                },
            )
        )
    for node in graph.nodes:
        if node.kind not in {"analysis_claim", "machine_experiment_claim"}:
            continue
        payload = node.payload or {}
        confidence = payload.get("confidence")
        result.append(
            AnalysisClaimRow(
                claim_id=claim_id(node.kind, node.id),
                claim_type=str(payload.get("claim_type") or node.kind),
                project=node.project,
                date=node.date,
                support_level=str(payload.get("support_level") or ""),
                confidence=float(confidence)
                if isinstance(confidence, (int, float))
                else 0.0,
                score=float(confidence)
                if isinstance(confidence, (int, float))
                else 0.0,
                summary=node.summary,
                source_ids=(node.id,),
                relation_ids=(),
                caveats=tuple(c.message for c in node.caveats),
                payload=dict(payload),
            )
        )
    return result


def _confidence_for_support(level: str) -> float:
    return {"strong": 0.85, "moderate": 0.65, "weak": 0.35}.get(level, 0.25)


def _relation_id(value: str) -> str:
    return value.split(": ", 1)[0]


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
    "analysis_claim_rows",
    "build_evidence_graph",
    "promote_graph_to_substrate",
]
