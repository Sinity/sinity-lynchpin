"""Reusable project/date context packs for analysis prompts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Iterable, Literal, Mapping, Sequence, cast

from ..core.evidence import EvidenceCaveat, dedupe_caveats
from ..core.evidence_graph import EvidenceGraph, EvidenceNode
from ..core.projects import canonical_project_name
from .causal_chains import CausalChain, detect_chains
from .current_state import CurrentStateEvidencePack, current_state_evidence_pack, evidence_pack_markdown
from .evidence_graph import build_evidence_graph
from .evidence_views import render_evidence_relations, render_evidence_timeline
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
    prefer_substrate: bool = False,
) -> ContextPack:
    """Build an LLM-facing context pack with explicit mode/caveats."""
    graph = None
    substrate_caveat = None
    if prefer_substrate:
        graph, substrate_caveat = _load_substrate_graph(
            start=start.date(),
            end=end.date(),
            projects=projects,
            mode=mode,
        )
    if graph is None:
        graph = build_evidence_graph(
            start=start.date(),
            end=end.date(),
            projects=projects,
            mode=mode,
            exclude_analysis_artifacts=exclude_analysis_artifacts,
        )
        if substrate_caveat is not None:
            graph = replace(graph, caveats=dedupe_caveats(graph.caveats + (substrate_caveat,)))
    return graph_context_pack(
        graph,
        start=start,
        end=end,
        projects=projects,
        semantic=semantic,
        persist_semantic=persist_semantic,
    )


def _load_substrate_graph(
    *,
    start: date,
    end: date,
    projects: Sequence[str] | None,
    mode: ContextPackMode,
) -> tuple[EvidenceGraph | None, EvidenceCaveat | None]:
    """Load a previously materialized graph from DuckDB when available."""
    try:
        from lynchpin.substrate import connect
        from lynchpin.substrate.graph import load_evidence_graph
    except ImportError as exc:
        return None, EvidenceCaveat("substrate", "missing", f"DuckDB substrate import failed: {exc}")
    try:
        with connect(read_only=True) as conn:
            graph = load_evidence_graph(
                conn,
                refresh_id=_current_state_refresh_id(
                    start=start,
                    end=end,
                    mode=mode,
                    projects=projects,
                ),
            )
            if graph is not None:
                return cast(EvidenceGraph, graph), None
            graph = load_evidence_graph(
                conn,
                start=start,
                end=end,
                mode=mode,
                projects=tuple(projects) if projects else None,
            )
    except Exception as exc:
        return None, EvidenceCaveat("substrate", "partial", f"DuckDB substrate read failed; rebuilt live graph: {exc}")
    if graph is None:
        return None, EvidenceCaveat("substrate", "partial", "No materialized DuckDB graph matched; rebuilt live graph.")
    return cast(EvidenceGraph, graph), None


def _current_state_refresh_id(
    *,
    start: date,
    end: date,
    mode: ContextPackMode,
    projects: Sequence[str] | None,
) -> str:
    project_key = ",".join(sorted(projects or ())) if projects else "all"
    return f"current-state:{start.isoformat()}:{end.isoformat()}:{mode}:{project_key}"


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
    machine_section = _render_machine_analysis_artifacts(
        start=pack.start.date(),
        end=pack.end.date(),
        projects=tuple(project.project for project in pack.projects),
    )
    if machine_section:
        lines.extend(["## Machine Analysis", "", machine_section, ""])
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


def _render_machine_analysis_artifacts(
    *,
    start: date,
    end: date,
    projects: Sequence[str],
) -> str:
    """Render compact machine-analysis summaries from materialized artifacts."""
    from ..analysis.core.io import load_analysis_artifact

    lines: list[str] = []
    episodes = _artifact_rows(load_analysis_artifact("machine_episode_analysis.json"), "episodes")
    matching_episodes = [
        row for row in episodes
        if _row_overlaps(row, start=start, end=end, start_key="started_at", end_key="ended_at")
    ]
    if matching_episodes:
        lines.append(f"- Episodes in window: {len(matching_episodes)} ({_top_counts(row.get('kind') for row in matching_episodes)})")

    context = _artifact_rows(load_analysis_artifact("machine_context_windows.json"), "windows")
    selected_projects = set(projects)
    matching_context = [
        row for row in context
        if _row_overlaps(row, start=start, end=end, start_key="started_at", end_key="ended_at")
        and (not selected_projects or selected_projects.intersection(_row_projects(row)))
    ]
    if matching_context:
        overlapped = sum(1 for row in matching_context if _row_int(row, "episode_count") > 0)
        lines.append(f"- Work windows with machine episodes: {overlapped}/{len(matching_context)}")

    states = load_analysis_artifact("machine_work_state_windows.json")
    if states is not None:
        pressure_states = states.get("pressure_state_counts")
        work_states = states.get("work_state_counts")
        if isinstance(pressure_states, dict) and isinstance(work_states, dict):
            lines.append(
                "- Work-state segmentation: "
                f"{states.get('window_count', 0)} windows; "
                f"pressure={_format_mapping_counts(pressure_states)}; "
                f"work={_format_mapping_counts(work_states)}"
            )

    commands = load_analysis_artifact("command_performance_windows.json")
    if commands is not None:
        tools = _artifact_rows(commands, "tools")
        if tools:
            tool_counts = {str(row.get("tool")): row.get("command_count") for row in tools if row.get("tool")}
            pressure_counts = {
                str(row.get("tool")): row.get("pressure_overlap_count")
                for row in tools
                if row.get("tool") and _row_int(row, "pressure_overlap_count") > 0
            }
            lines.append(
                "- Command performance: "
                f"{commands.get('command_count', 0)} commands; "
                f"tools={_format_mapping_counts(tool_counts)}; "
                f"pressure-overlap={_format_mapping_counts(pressure_counts)}"
            )

    deltas = load_analysis_artifact("machine_observational_deltas.json")
    if deltas is not None:
        delta_rows = _artifact_rows(deltas, "deltas")
        if delta_rows:
            top = sorted(
                delta_rows,
                key=lambda row: _row_float(row, "median_delta_seconds"),
                reverse=True,
            )[:3]
            rendered = ", ".join(
                f"{row.get('tool')}/{row.get('work_state')}/{row.get('pressure_state')} "
                f"medianΔ={row.get('median_delta_seconds')}s"
                for row in top
            )
            lines.append(f"- Observational command deltas: {len(delta_rows)} matched cohorts; {rendered}")

    devshell = load_analysis_artifact("devshell_performance.json")
    if devshell is not None:
        summaries = _artifact_rows(devshell, "summaries")
        if summaries:
            class_counts = {str(row.get("command_class")): row.get("command_count") for row in summaries if row.get("command_class")}
            lines.append(
                "- Devshell/Nix performance: "
                f"{devshell.get('command_count', 0)} commands; "
                f"classes={_format_mapping_counts(class_counts)}"
            )

    attribution = load_analysis_artifact("machine_below_attribution.json")
    if attribution is not None:
        unattributed = attribution.get("unattributed_pressure_episode_count")
        pressure = attribution.get("pressure_episode_count")
        if pressure is not None:
            lines.append(f"- Below attribution: {unattributed}/{pressure} pressure episodes lack bounded below overlap")

    baselines = load_analysis_artifact("machine_observational_baselines.json")
    if baselines is not None:
        caveats = _row_list(baselines, "caveats")
        hardware = _row_list(baselines, "by_hardware_regime")
        lines.append(f"- Observational baselines: {len(hardware)} hardware regimes; caveats: {len(caveats)}")

    claims = load_analysis_artifact("machine_experiment_claims.json")
    if claims is not None:
        lines.append(
            "- Experiment claim packs: "
            f"{claims.get('controlled_claim_count', 0)} controlled / "
            f"{claims.get('observational_claim_count', 0)} observational"
        )

    readiness = load_analysis_artifact("machine_analysis_readiness.json")
    if readiness is not None:
        dimensions = _artifact_rows(readiness, "dimensions")
        if dimensions:
            statuses = _top_counts(row.get("status") for row in dimensions)
            lines.append(f"- Machine analysis readiness: {statuses}")

    if not lines:
        return ""
    lines.append("")
    lines.append("_Machine analysis is observational unless a manifest-backed controlled claim pack says otherwise._")
    return "\n".join(lines)


def _artifact_rows(payload: object, key: str) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _row_projects(row: dict[str, object]) -> set[str]:
    projects = row.get("projects")
    if not isinstance(projects, list):
        return set()
    return {str(project) for project in projects if project}


def _row_int(row: dict[str, object], key: str) -> int:
    value = row.get(key)
    return int(value) if isinstance(value, (int, float, str)) and str(value).strip() else 0


def _row_float(row: dict[str, object], key: str) -> float:
    value = row.get(key)
    try:
        return float(str(value or 0.0))
    except ValueError:
        return 0.0


def _row_list(row: dict[str, object], key: str) -> list[object]:
    value = row.get(key)
    return value if isinstance(value, list) else []


def _row_overlaps(row: dict[str, object], *, start: date, end: date, start_key: str, end_key: str) -> bool:
    row_start = str(row.get(start_key) or "")[:10]
    row_end = str(row.get(end_key) or "")[:10] or row_start
    if not row_start:
        return False
    return row_end >= start.isoformat() and row_start <= end.isoformat()


def _top_counts(values: Iterable[object], *, limit: int = 4) -> str:
    counts: dict[str, int] = {}
    for value in values:
        if value:
            key = str(value)
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return "none"
    return ", ".join(f"{key}×{count}" for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit])


def _format_mapping_counts(mapping: Mapping[str, object], *, limit: int = 4) -> str:
    counts: dict[str, int] = {}
    for key, value in mapping.items():
        try:
            count = int(str(value))
        except ValueError:
            continue
        if count:
            counts[str(key)] = count
    if not counts:
        return "none"
    return ", ".join(f"{key}×{count}" for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit])


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
