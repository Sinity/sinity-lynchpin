"""Reusable project/date context packs for analysis prompts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Sequence

from ..core.projects import canonical_project_name
from .current_state import CurrentStateEvidencePack, current_state_evidence_pack, evidence_pack_markdown
from .evidence import EvidenceCaveat, dedupe_caveats
from .evidence_graph import EvidenceGraph, build_evidence_graph, render_evidence_relations, render_evidence_timeline
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
    caveats: tuple[EvidenceCaveat, ...]


def context_pack(
    *,
    start: datetime,
    end: datetime,
    projects: Sequence[str] | None = None,
    mode: ContextPackMode = "local-fast",
    semantic: bool = False,
    persist_semantic: bool = False,
) -> ContextPack:
    """Build an LLM-facing context pack with explicit mode/caveats."""
    graph = build_evidence_graph(
        start=start.date(),
        end=end.date(),
        projects=projects,
        mode=mode,
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
        caveats=_pack_caveats(evidence_pack=evidence_pack, graph=graph),
    )


def render_context_pack(pack: ContextPack) -> str:
    """Render a context pack for prompt inclusion."""
    lines = [
        f"# Context Pack ({pack.start.date().isoformat()} → {pack.end.date().isoformat()})",
        "",
        f"- Mode: `{pack.mode}`",
        f"- Generated: {pack.generated_at.isoformat(timespec='seconds')}",
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
    if pack.semantic_enrichment is not None:
        lines.extend(
            [
                "## Semantic Enrichment",
                "",
                render_semantic_summary(pack.semantic_enrichment),
                "",
            ]
        )
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
    if any("polylogue" in row.sources for row in rows):
        caveats.append(EvidenceCaveat("polylogue", "partial", "AI chat rows should be inspected as pointers, not verbatim transcript evidence."))
    return tuple(caveats)


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
