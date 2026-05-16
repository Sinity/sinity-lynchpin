"""Prompt-facing projections over evidence graphs."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Sequence

from ..core.parse import as_local
from ..core.evidence_graph import (
    EvidenceGraph,
    EvidenceRelation,
    EvidenceRelationEntry,
    EvidenceTimelineEntry,
)
from .evidence_projects import include_project, selected_projects


def render_evidence_graph_summary(graph: EvidenceGraph) -> str:
    """Render compact graph coverage for prompt-facing reports."""
    by_kind: dict[str, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)
    by_relation: dict[str, int] = defaultdict(int)
    for node in graph.nodes:
        by_kind[node.kind] += 1
        by_source[node.source] += 1
    for edge in graph.edges:
        by_relation[edge.relation] += 1
    return "\n".join(
        [
            f"- Nodes: {len(graph.nodes)} ({_format_counts(by_kind)})",
            f"- Sources: {_format_counts(by_source)}",
            f"- Edges: {len(graph.edges)} ({_format_counts(by_relation)})",
            f"- Projects: {', '.join(sorted({node.project for node in graph.nodes if node.project})) or '(none)'}",
        ]
    )


def evidence_timeline(
    graph: EvidenceGraph,
    *,
    limit: int = 32,
    projects: Sequence[str] | None = None,
    include_analysis_artifacts: bool = False,
) -> tuple[EvidenceTimelineEntry, ...]:
    """Project the graph into chronological evidence rows."""
    selected = selected_projects(projects)
    entries = []
    for node in graph.nodes:
        if node.kind == "analysis_artifact" and not include_analysis_artifacts:
            continue
        if not include_project(node.project, selected):
            continue
        entries.append(
            EvidenceTimelineEntry(
                node_id=node.id,
                date=node.date,
                when=node.start,
                project=node.project,
                source=node.source,
                kind=node.kind,
                summary=node.summary,
            )
        )
    return tuple(sorted(entries, key=_timeline_entry_key)[: max(0, limit)])


def render_evidence_timeline(
    graph: EvidenceGraph,
    *,
    limit: int = 32,
    projects: Sequence[str] | None = None,
    include_analysis_artifacts: bool = False,
) -> str:
    """Render chronological graph evidence as a compact Markdown table."""
    rows = evidence_timeline(
        graph,
        limit=limit,
        projects=projects,
        include_analysis_artifacts=include_analysis_artifacts,
    )
    lines = [
        "| When | Project | Source | Kind | Evidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not rows:
        lines.append(
            "| _none_ | _none_ | _none_ | _none_ | _No chronological evidence matched._ |"
        )
        return "\n".join(lines)
    for row in rows:
        cells = (
            _markdown_cell(_format_timeline_when(row)),
            _markdown_cell(row.project or "unattributed"),
            _markdown_cell(row.source),
            _markdown_cell(row.kind),
            _markdown_cell(row.summary),
        )
        lines.append(f"| {' | '.join(cells)} |")
    return "\n".join(lines)


def evidence_relations(
    graph: EvidenceGraph,
    *,
    limit: int = 16,
    projects: Sequence[str] | None = None,
    relation_types: Sequence[EvidenceRelation] = (
        "references",
        "temporal_overlap",
        "temporal_proximity",
    ),
) -> tuple[EvidenceRelationEntry, ...]:
    """Project graph edges into compact prompt-facing relationship rows."""
    selected = selected_projects(projects)
    wanted = set(relation_types)
    nodes = graph.node_map()
    rows = []
    for edge in graph.edges:
        if wanted and edge.relation not in wanted:
            continue
        source = nodes.get(edge.source_id)
        target = nodes.get(edge.target_id)
        if source is None or target is None:
            continue
        project = (
            source.project
            if source.project == target.project
            else source.project or target.project
        )
        if not include_project(project, selected):
            continue
        rows.append(
            EvidenceRelationEntry(
                source_node_id=edge.source_id,
                target_node_id=edge.target_id,
                source_source=source.source,
                target_source=target.source,
                relation=edge.relation,
                evidence=edge.evidence,
                weight=edge.weight,
                date=min(source.date, target.date),
                project=project,
                source_summary=source.summary,
                target_summary=target.summary,
            )
        )
    return tuple(sorted(rows, key=_relation_entry_key)[: max(0, limit)])


def render_evidence_relations(
    graph: EvidenceGraph,
    *,
    limit: int = 16,
    projects: Sequence[str] | None = None,
    relation_types: Sequence[EvidenceRelation] = (
        "references",
        "temporal_overlap",
        "temporal_proximity",
    ),
) -> str:
    """Render important graph relationships as a compact Markdown table."""
    rows = evidence_relations(
        graph,
        limit=limit,
        projects=projects,
        relation_types=relation_types,
    )
    lines = [
        "| Date | Project | Relation | Evidence | Source | Target |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if not rows:
        lines.append("| _none_ | _none_ | _none_ | _none_ | _none_ | _none_ |")
        return "\n".join(lines)
    for row in rows:
        cells = (
            row.date.isoformat(),
            row.project or "unattributed",
            row.relation,
            row.evidence,
            row.source_summary,
            row.target_summary,
        )
        lines.append(f"| {' | '.join(_markdown_cell(cell) for cell in cells)} |")
    return "\n".join(lines)


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "(none)"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _timeline_entry_key(
    entry: EvidenceTimelineEntry,
) -> tuple[date, int, str, str, str, str]:
    timed = entry.when is not None
    when = _timeline_sort_stamp(entry)
    return (
        entry.date,
        0 if timed else 1,
        when,
        entry.project or "",
        entry.source,
        entry.node_id,
    )


def _format_timeline_when(entry: EvidenceTimelineEntry) -> str:
    if entry.when is None:
        return f"{entry.date.isoformat()} (logical day)"
    return as_local(entry.when).isoformat(timespec="minutes")


def _timeline_sort_stamp(entry: EvidenceTimelineEntry) -> str:
    if entry.when is None:
        return datetime.combine(entry.date, datetime.min.time()).isoformat()
    return as_local(entry.when).isoformat()


def _relation_entry_key(
    entry: EvidenceRelationEntry,
) -> tuple[date, float, str, str, str]:
    return (
        entry.date,
        -entry.weight,
        entry.project or "",
        entry.relation,
        f"{entry.source_node_id}:{entry.target_node_id}",
    )


def _markdown_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


__all__ = [
    "evidence_relations",
    "evidence_timeline",
    "render_evidence_graph_summary",
    "render_evidence_relations",
    "render_evidence_timeline",
]
