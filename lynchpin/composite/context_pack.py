"""Reusable project/date context packs for analysis prompts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Sequence

from ..core.projects import canonical_project_name
from .causal_chains import CausalChain, detect_chains
from .current_state import CurrentStateEvidencePack, current_state_evidence_pack, evidence_pack_markdown
from .evidence import EvidenceCaveat, dedupe_caveats
from .evidence_graph import EvidenceGraph, EvidenceNode, build_evidence_graph, render_evidence_relations, render_evidence_timeline
from .semantic_enrichment import SemanticEnrichment, build_semantic_enrichment, render_semantic_summary
from .work_correlation import CorrelatedWorkDay, DatasetCorrelation, WorkEvidenceClaim, render_work_day_correlations, strongest_work_correlations
from .work_correlation import dataset_correlations, render_dataset_correlations, render_supported_work_claims, supported_work_claims

ContextPackMode = Literal["local-fast", "local-heavy", "network"]


@dataclass(frozen=True)
class ProjectContextSlice:
    project: str
    rows: tuple[CorrelatedWorkDay, ...]
    caveats: tuple[EvidenceCaveat, ...]


@dataclass(frozen=True)
class ContextPack:
    start: datetime
    end: datetime
    generated_at: datetime
    mode: ContextPackMode
    graph: EvidenceGraph
    semantic_enrichment: SemanticEnrichment | None
    evidence_pack: CurrentStateEvidencePack
    dataset_correlations: tuple[DatasetCorrelation, ...]
    claims: tuple[WorkEvidenceClaim, ...]
    projects: tuple[ProjectContextSlice, ...]
    salient_chains: tuple[CausalChain, ...]
    salient_anomalies: tuple[EvidenceNode, ...]
    readiness_forecast: EvidenceNode | None
    caveats: tuple[EvidenceCaveat, ...]


def context_pack(
    *,
    start: datetime,
    end: datetime,
    projects: Sequence[str] | None = None,
    mode: ContextPackMode = "local-fast",
    semantic: bool = False,
    persist_semantic: bool = False,
    exclude_analysis_artifacts: Sequence[str] = (),
) -> ContextPack:
    """Build an LLM-facing context pack with explicit mode/caveats."""
    graph = build_evidence_graph(
        start=start.date(),
        end=end.date(),
        projects=projects,
        mode=mode,
        exclude_analysis_artifacts=exclude_analysis_artifacts,
    )
    return graph_context_pack(
        graph,
        start=start,
        end=end,
        projects=projects,
        semantic=semantic,
        persist_semantic=persist_semantic,
    )


def graph_context_pack(
    graph: EvidenceGraph,
    *,
    start: datetime,
    end: datetime,
    projects: Sequence[str] | None = None,
    semantic: bool = False,
    persist_semantic: bool = False,
) -> ContextPack:
    """Build a context pack from a prebuilt evidence graph."""
    evidence_pack = current_state_evidence_pack(
        start=start,
        end=end,
        projects=projects,
        include_github_frontier=graph.mode == "network",
        graph=graph,
        mode=graph.mode,
    )
    slices = _project_slices(evidence_pack.work_correlations, projects=projects)
    dataset_rows = dataset_correlations(graph, limit=16)
    claims = supported_work_claims(evidence_pack.work_correlations, graph=graph, limit=24)
    chains = _select_top_chains(graph, limit=5)
    anomalies = _select_top_anomalies(graph, limit=5)
    readiness = _select_readiness(graph)
    return ContextPack(
        start=start,
        end=end,
        generated_at=datetime.now(timezone.utc),
        mode=graph.mode,
        graph=graph,
        semantic_enrichment=build_semantic_enrichment(graph, persist=persist_semantic) if semantic else None,
        evidence_pack=evidence_pack,
        dataset_correlations=dataset_rows,
        claims=claims,
        projects=slices,
        salient_chains=chains,
        salient_anomalies=anomalies,
        readiness_forecast=readiness,
        caveats=_pack_caveats(evidence_pack=evidence_pack, graph=graph),
    )


def render_context_pack(pack: ContextPack) -> str:
    """Render a context pack for prompt inclusion."""
    from .substrate_confidence import (
        build_substrate_confidence_matrix,
        render_substrate_confidence_matrix,
    )

    matrix = build_substrate_confidence_matrix(
        readiness=pack.evidence_pack.source_readiness,
        graph=pack.graph,
        correlation_rows=pack.evidence_pack.work_correlations,
    )
    lines = [
        f"# Context Pack ({pack.start.date().isoformat()} → {pack.end.date().isoformat()})",
        "",
        f"- Mode: `{pack.mode}`",
        f"- Generated: {pack.generated_at.isoformat(timespec='seconds')}",
        "",
        "## Substrate Confidence",
        "",
        render_substrate_confidence_matrix(matrix),
        "",
        "## Shared Evidence",
        "",
        evidence_pack_markdown(pack.evidence_pack),
        "",
        "## Chronological Evidence",
        "",
        render_evidence_timeline(pack.graph, limit=32),
        "",
        "## Graph Relations",
        "",
        render_evidence_relations(pack.graph, limit=16),
        "",
        "## Dataset Correlations",
        "",
        render_dataset_correlations(pack.dataset_correlations),
        "",
        "## Supported Work Claims",
        "",
        render_supported_work_claims(pack.claims[:12]),
        "",
    ]
    if pack.salient_chains or pack.salient_anomalies or pack.readiness_forecast is not None:
        lines.extend(["## Temporal Signals", ""])
        if pack.readiness_forecast is not None:
            lines.append(f"**Readiness:** {pack.readiness_forecast.summary}")
            lines.append("")
        if pack.salient_anomalies:
            lines.append("**Anomalies:**")
            for n in pack.salient_anomalies:
                lines.append(f"- {n.date.isoformat()} — {n.summary}")
            lines.append("")
        if pack.salient_chains:
            lines.append("**Causal chains:**")
            for c in pack.salient_chains:
                lines.append(f"- {c.date.isoformat()} — {c.summary} (confidence {c.confidence:.0%})")
            lines.append("")
    if pack.semantic_enrichment is not None:
        lines.extend(
            [
                "## Semantic Enrichment",
                "",
                render_semantic_summary(pack.semantic_enrichment),
                "",
            ]
        )
    work_event_section = _render_work_event_coverage(pack.graph)
    if work_event_section:
        lines.extend(["## AI Work-Event Coverage", "", work_event_section, ""])
    closure_section = _render_issue_closure_chains(pack.graph)
    if closure_section:
        lines.extend(["## Issue Closure Chains", "", closure_section, ""])
    relationships_section = _render_project_relationships(pack.graph)
    if relationships_section:
        lines.extend(["## Cross-Project Relationships", "", relationships_section, ""])
    lines.extend(["## Project Slices", ""])
    if not pack.projects:
        lines.append("_No project-specific correlated rows matched the selection._")
    for project in pack.projects:
        lines.extend(
            [
                f"### {project.project}",
                "",
                render_work_day_correlations(strongest_work_correlations(project.rows, limit=8)),
                "",
            ]
        )
        if project.caveats:
            lines.extend(["Caveats:"])
            lines.extend(f"- {caveat.source}: {caveat.message}" for caveat in project.caveats)
            lines.append("")
    if pack.caveats:
        lines.extend(["## Pack Caveats", ""])
        lines.extend(f"- {caveat.source}: {caveat.message}" for caveat in pack.caveats)
    return "\n".join(lines).rstrip()


def _selected_projects(projects: Sequence[str] | None) -> set[str]:
    if not projects:
        return set()
    return {
        project
        for project in (canonical_project_name(value) for value in projects)
        if project is not None
    }


def _project_slices(
    rows: Sequence[CorrelatedWorkDay],
    *,
    projects: Sequence[str] | None,
) -> tuple[ProjectContextSlice, ...]:
    selected = _selected_projects(projects)
    if selected:
        rows = tuple(row for row in rows if row.project in selected)

    grouped: dict[str, list[CorrelatedWorkDay]] = {}
    for row in rows:
        grouped.setdefault(row.project, []).append(row)
    return tuple(
        ProjectContextSlice(project=project, rows=tuple(project_rows), caveats=_project_caveats(project_rows))
        for project, project_rows in sorted(grouped.items())
    )


def _project_caveats(rows: Sequence[CorrelatedWorkDay]) -> tuple[EvidenceCaveat, ...]:
    caveats = []
    if rows and not any(row.has_cross_source_support for row in rows):
        caveats.append(EvidenceCaveat("correlation", "partial", "Project rows have only single-source support in this window."))
    if any("github" in row.sources for row in rows):
        caveats.append(EvidenceCaveat("github", "partial", "GitHub rows require lifecycle interpretation before workload conclusions."))
    if any("github_ref" in row.sources for row in rows):
        caveats.append(EvidenceCaveat("github_ref", "partial", "GitHub refs are commit-subject references unless network frontier evidence is enabled."))
    if any("polylogue" in row.sources for row in rows):
        caveats.append(EvidenceCaveat("polylogue", "partial", "AI chat rows should be inspected as pointers, not verbatim transcript evidence."))
    return tuple(caveats)


def _select_top_chains(graph: EvidenceGraph, *, limit: int) -> tuple[CausalChain, ...]:
    chains = detect_chains(graph.nodes, max_gap_minutes=60)
    return tuple(sorted(chains, key=lambda c: c.confidence, reverse=True)[:limit])


def _select_top_anomalies(graph: EvidenceGraph, *, limit: int) -> tuple[EvidenceNode, ...]:
    anomalies = [n for n in graph.nodes if n.kind == "temporal_anomaly"]
    anomalies.sort(
        key=lambda n: float(n.payload.get("score", 0)) if n.payload else 0.0,
        reverse=True,
    )
    return tuple(anomalies[:limit])


def _select_readiness(graph: EvidenceGraph) -> EvidenceNode | None:
    for n in graph.nodes:
        if n.kind == "readiness_forecast":
            return n
    return None


def _render_work_event_coverage(graph: EvidenceGraph, *, max_chains: int = 6) -> str:
    """Render Polylogue work-event coverage and top file-overlap chains.

    Returns empty string when no work-events are present so the section is
    silently omitted in modes that filtered them out.
    """
    work_events = [n for n in graph.nodes if n.kind == "ai_work_event"]
    if not work_events:
        return ""

    by_project_day_kind: dict[tuple[str, str, str], int] = {}
    for node in work_events:
        if not node.project:
            continue
        payload = node.payload or {}
        kind = payload.get("kind") or "unknown"
        # Arc K.3 tier maps directly to render decoration:
        #   low    → "?kind"  (heuristic, single-feature, or sub-0.5 confidence)
        #   medium → "kind"
        #   high   → "kind"   (agreement or strong overlay features)
        # If tier is missing (older payloads), fall back to confidence threshold.
        tier = payload.get("kind_tier")
        if tier == "low":
            rendered_kind = f"?{kind}"
        elif tier in ("medium", "high"):
            rendered_kind = kind
        else:
            confidence = float(payload.get("kind_confidence") or 0.0)
            rendered_kind = kind if confidence >= 0.5 else f"?{kind}"
        key = (node.project, node.date.isoformat(), rendered_kind)
        by_project_day_kind[key] = by_project_day_kind.get(key, 0) + 1

    if not by_project_day_kind:
        return ""

    lines: list[str] = []
    by_project_day: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for (project, day, kind), count in by_project_day_kind.items():
        by_project_day.setdefault((project, day), []).append((kind, count))

    last_project: str | None = None
    for (project, day) in sorted(by_project_day):
        if project != last_project:
            lines.append(f"**{project}**")
            last_project = project
        kinds = sorted(by_project_day[(project, day)], key=lambda kc: (-kc[1], kc[0]))
        kinds_str = ", ".join(f"{kind}×{count}" for kind, count in kinds)
        lines.append(f"- {day}: {kinds_str}")

    file_overlap_edges = [edge for edge in graph.edges if edge.relation == "file_overlap"]
    if file_overlap_edges:
        # rank by number of shared paths embedded in the evidence string
        def shared_path_count(evidence: str) -> int:
            base = evidence.split("(")[0]
            return base.count(",") + 1 if "shared paths" in base else 0
        file_overlap_edges.sort(key=lambda e: shared_path_count(e.evidence), reverse=True)
        lines.append("")
        lines.append("**Top file-overlap chains (AI work-event ↔ commit):**")
        for edge in file_overlap_edges[:max_chains]:
            lines.append(f"- {edge.evidence}")
        lines.append("")
        lines.append(
            "_Caveat: file_paths/tools_used and kind labels are heuristic Polylogue "
            "outputs. Co-occurrence; not authorship. See Arc K caveats._"
        )

    return "\n".join(lines)


def _render_issue_closure_chains(graph: EvidenceGraph, *, limit: int = 12) -> str:
    """Closure-chain section: only emits when at least one chain is broken,
    partial, or orphaned. Complete-only graphs add no useful prose.

    Per M.9, also includes per-project closure SLOs (median/p75/p90 days
    to close, broken/orphaned/partial/stale-tracking counts) so the reader
    sees both the specific bad chains AND the aggregate health curve.
    """
    from .closure_slos import compute_closure_slos, render_closure_slos
    from .issue_closure_chain import (
        closure_chain_summary,
        detect_closure_chains,
        render_issue_closure_chains,
    )

    chains = detect_closure_chains(graph)
    if not chains:
        return ""
    summary = closure_chain_summary(chains)
    interesting = summary["broken_or_orphaned"] + summary["by_status"].get("partial", 0)
    if interesting == 0:
        # Everything is complete — no diagnostic value worth pack space.
        return ""
    table = render_issue_closure_chains(chains, limit=limit)
    counts = ", ".join(
        f"{status}×{summary['by_status'][status]}"
        for status in ("broken", "orphaned", "partial", "complete")
        if summary["by_status"].get(status)
    )
    slos = compute_closure_slos(chains)
    slo_table = render_closure_slos(slos)
    return f"_Status: {counts}_\n\n{table}\n\n**Per-project closure SLOs (M.9):**\n\n{slo_table}"


def _render_project_relationships(graph: EvidenceGraph, *, limit: int = 12) -> str:
    """M.11 — emit only when at least one cross-project edge exists."""
    from .project_relationships import (
        build_project_relationships,
        render_project_relationships,
    )

    rel_graph = build_project_relationships(graph)
    if not rel_graph.relationships:
        return ""
    return render_project_relationships(rel_graph, limit=limit)


def _pack_caveats(*, evidence_pack: CurrentStateEvidencePack, graph: EvidenceGraph) -> tuple[EvidenceCaveat, ...]:
    caveats = tuple(evidence_pack.source_readiness.caveats) + evidence_pack.movement.caveats + tuple(graph.caveats)
    return dedupe_caveats(caveats)


__all__ = [
    "ContextPack",
    "ContextPackMode",
    "ProjectContextSlice",
    "context_pack",
    "graph_context_pack",
    "render_context_pack",
]
