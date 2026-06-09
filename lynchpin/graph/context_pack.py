"""Reusable project/date context packs for analysis prompts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Literal, Mapping, Sequence, cast

from ..core.evidence import EvidenceCaveat, dedupe_caveats
from ..core.evidence_graph import EvidenceGraph, EvidenceNode
from ..core.projects import canonical_project_name
from .causal_chains import CausalChain, detect_chains
from .current_state import CurrentStateEvidencePack, current_state_evidence_pack, evidence_pack_markdown
from .evidence_graph import build_evidence_graph
from .evidence_views import render_evidence_relations, render_evidence_timeline
from .weak_tags import WeakTagEnrichment, build_weak_tags, render_weak_tag_summary
from .work_correlation import CorrelatedWorkDay, DatasetCorrelation, WorkEvidenceClaim, render_work_day_correlations, strongest_work_correlations
from .work_correlation import dataset_correlations, render_dataset_correlations, render_supported_work_claims, supported_work_claims

SubstrateGraphStatus = Literal[
    "disabled",
    "exact_hit",
    "compatible_hit",
    "materialize_rebuild",
    "missing",
    "read_error",
    "required_miss",
]


class ContextPackSubstrateRequiredError(RuntimeError):
    """Raised when a caller requires a materialized substrate graph."""


@dataclass(frozen=True)
class ContextPackSubstrateState:
    status: SubstrateGraphStatus
    refresh_id: str | None
    message: str


@dataclass(frozen=True)
class ProjectContextSlice:
    project: str
    rows: tuple[CorrelatedWorkDay, ...]
    caveats: tuple[EvidenceCaveat, ...]


@dataclass(frozen=True)
class PhysiologySummary:
    """Operator physiological state over the window, from health + sleep sources.

    Missing days are excluded from every mean (never counted as zero); each
    metric carries the number of days that actually had data. A ``None`` mean
    means no measured data for that signal in the window — not a measured zero.
    These signals are export-bound and frequently stale, so observed last-dates
    and a coverage caveat are surfaced rather than implied.
    """

    window_start: date
    window_end: date
    sleep_hours_mean: float | None
    sleep_days: int
    sleep_score_mean: float | None
    stress_mean: float | None
    stress_days: int
    hrv_rmssd_mean: float | None
    hrv_days: int
    resting_hr_mean: float | None
    resting_hr_days: int
    steps_mean: float | None
    steps_days: int
    last_health_date: date | None
    last_sleep_date: date | None
    caveats: tuple[EvidenceCaveat, ...]


@dataclass(frozen=True)
class ContextPack:
    start: datetime
    end: datetime
    generated_at: datetime
    mode: str
    graph: EvidenceGraph
    substrate_state: ContextPackSubstrateState
    weak_tags: WeakTagEnrichment | None
    evidence_pack: CurrentStateEvidencePack
    dataset_correlations: tuple[DatasetCorrelation, ...]
    claims: tuple[WorkEvidenceClaim, ...]
    projects: tuple[ProjectContextSlice, ...]
    salient_chains: tuple[CausalChain, ...]
    salient_anomalies: tuple[EvidenceNode, ...]
    readiness_forecast: EvidenceNode | None
    caveats: tuple[EvidenceCaveat, ...]
    physiology: PhysiologySummary | None = None


def context_pack(
    *,
    start: datetime,
    end: datetime,
    projects: Sequence[str] | None = None,
    include_github_frontier: bool = False,
    weak_tags: bool = False,
    persist_weak_tags: bool = False,
    exclude_analysis_artifacts: Sequence[str] = (),
    prefer_substrate: bool = False,
    materialize_substrate: bool = False,
) -> ContextPack:
    """Build an LLM-facing context pack with explicit substrate state."""
    graph = None
    substrate_caveat = None
    refresh_id = _current_state_refresh_id(
        start=start.date(),
        end=end.date(),
        projects=projects,
    )
    substrate_state = ContextPackSubstrateState(
        status="disabled",
        refresh_id=None,
        message="Substrate graph lookup disabled.",
    )
    if prefer_substrate and materialize_substrate:
        substrate_caveat = EvidenceCaveat(
            "substrate",
            "partial",
            "Substrate materialization requested; rebuilt and materialized live graph.",
        )
        substrate_state = ContextPackSubstrateState(
            status="materialize_rebuild",
            refresh_id=refresh_id,
            message="Substrate materialization requested; rebuilt and materialized live graph.",
        )
    elif prefer_substrate:
        graph, substrate_caveat, substrate_state = _load_substrate_graph(
            start=start.date(),
            end=end.date(),
            projects=projects,
            refresh_id=refresh_id,
        )
    if graph is None:
        if prefer_substrate and not materialize_substrate:
            required_state = ContextPackSubstrateState(
                status="required_miss",
                refresh_id=refresh_id,
                message=substrate_state.message,
            )
            raise ContextPackSubstrateRequiredError(required_state.message)
        graph = build_evidence_graph(
            start=start.date(),
            end=end.date(),
            projects=projects,
            include_github_frontier=include_github_frontier,
            exclude_analysis_artifacts=exclude_analysis_artifacts,
        )
        if substrate_caveat is not None:
            graph = replace(graph, caveats=dedupe_caveats(graph.caveats + (substrate_caveat,)))
        if materialize_substrate:
            _materialize_context_graph(
                graph,
                refresh_id=refresh_id,
                projects=projects,
            )
    return graph_context_pack(
        graph,
        start=start,
        end=end,
        projects=projects,
        weak_tags=weak_tags,
        persist_weak_tags=persist_weak_tags,
        substrate_state=substrate_state,
    )


def _load_substrate_graph(
    *,
    start: date,
    end: date,
    projects: Sequence[str] | None,
    refresh_id: str,
) -> tuple[EvidenceGraph | None, EvidenceCaveat | None, ContextPackSubstrateState]:
    """Load a previously materialized graph from DuckDB when available."""
    try:
        from lynchpin.substrate import connect
        from lynchpin.substrate.graph import load_evidence_graph
    except ImportError as exc:
        message = f"DuckDB substrate import failed: {exc}"
        return (
            None,
            EvidenceCaveat("substrate", "missing", message),
            ContextPackSubstrateState("read_error", refresh_id, message),
        )
    try:
        with connect(read_only=True) as conn:
            graph = load_evidence_graph(
                conn,
                refresh_id=refresh_id,
            )
            if graph is not None:
                return (
                    cast(EvidenceGraph, graph),
                    None,
                    ContextPackSubstrateState("exact_hit", refresh_id, "Loaded exact materialized DuckDB graph."),
                )
            graph = load_evidence_graph(
                conn,
                start=start,
                end=end,
                projects=tuple(projects) if projects else None,
            )
    except Exception as exc:
        message = f"DuckDB substrate read failed: {exc}"
        return (
            None,
            EvidenceCaveat("substrate", "partial", message),
            ContextPackSubstrateState("read_error", refresh_id, message),
        )
    if graph is None:
        message = "No materialized DuckDB graph matched."
        return (
            None,
            EvidenceCaveat("substrate", "partial", message),
            ContextPackSubstrateState("missing", refresh_id, message),
        )
    return (
        cast(EvidenceGraph, graph),
        None,
        ContextPackSubstrateState("compatible_hit", refresh_id, "Loaded compatible materialized DuckDB graph."),
    )


def _current_state_refresh_id(
    *,
    start: date,
    end: date,
    projects: Sequence[str] | None,
) -> str:
    project_key = ",".join(sorted(projects or ())) if projects else "all"
    return f"current-state:{start.isoformat()}:{end.isoformat()}:{project_key}"


def _materialize_context_graph(
    graph: EvidenceGraph,
    *,
    refresh_id: str,
    projects: Sequence[str] | None,
) -> None:
    from .evidence_graph import promote_graph_to_substrate

    promote_graph_to_substrate(
        graph,
        refresh_id=refresh_id,
        projects=tuple(projects or ()),
    )


def _mean_present(values: Iterable[float | int | None]) -> tuple[float | None, int]:
    """Mean over the present (non-None) values; (None, 0) when all are missing."""
    present = [float(v) for v in values if v is not None]
    if not present:
        return None, 0
    return sum(present) / len(present), len(present)


def _build_physiology(*, start: datetime, end: datetime) -> PhysiologySummary | None:
    """Summarize operator physiology (sleep + health) over the window.

    Sourced from the canonical personal daily-signal product, with missing-day
    exclusion and an explicit coverage caveat when observed physiology data ends
    before the requested window. Returns None when neither source has any data
    in the window — so the section is omitted rather than rendering empty rows.
    """
    from ..materialization import ensure_materialized
    from ..sources.personal_signals import iter_personal_daily_signals

    s, e = start.date(), end.date()
    end_exclusive = e + timedelta(days=1)
    try:
        ensure_materialized("personal_daily_signals", window=(s, end_exclusive))
    except Exception:
        pass
    try:
        signals = tuple(iter_personal_daily_signals(start=s, end=end_exclusive, ensure=False))
    except Exception:
        signals = ()

    health_by_day: dict[date, dict[str, float]] = {}
    sleep_by_day: dict[date, dict[str, float]] = {}
    for signal in signals:
        if signal.source == "health":
            health_by_day.setdefault(signal.date, {})[signal.metric] = signal.value
        elif signal.source == "sleep":
            sleep_by_day.setdefault(signal.date, {})[signal.metric] = signal.value

    if not health_by_day and not sleep_by_day:
        return None

    sleep_hours_mean, sleep_days = _mean_present(
        (v / 60.0)
        for row in sleep_by_day.values()
        if (v := row.get("sleep_minutes", row.get("sleep_arch_total_minutes"))) is not None
    )
    sleep_score_mean, _ = _mean_present(row.get("sleep_score") for row in sleep_by_day.values())
    stress_mean, stress_days = _mean_present(row.get("stress_avg") for row in health_by_day.values())
    hrv_mean, hrv_days = _mean_present(row.get("hrv_rmssd") for row in health_by_day.values())
    resting_hr_mean, resting_hr_days = _mean_present(row.get("resting_heart_rate") for row in health_by_day.values())
    steps_mean, steps_days = _mean_present(row.get("steps") for row in health_by_day.values())
    last_health = max(health_by_day, default=None)
    last_sleep = max(sleep_by_day, default=None)

    caveats: list[EvidenceCaveat] = []
    observed_last = max([d for d in (last_health, last_sleep) if d is not None], default=None)
    if observed_last is not None and observed_last < e:
        caveats.append(
            EvidenceCaveat(
                "physiology",
                "partial",
                f"Physiology signals end {observed_last.isoformat()}, before the window "
                f"end {e.isoformat()}; means cover only the observed days, not the full window.",
            )
        )

    return PhysiologySummary(
        window_start=s,
        window_end=e,
        sleep_hours_mean=sleep_hours_mean,
        sleep_days=sleep_days,
        sleep_score_mean=sleep_score_mean,
        stress_mean=stress_mean,
        stress_days=stress_days,
        hrv_rmssd_mean=hrv_mean,
        hrv_days=hrv_days,
        resting_hr_mean=resting_hr_mean,
        resting_hr_days=resting_hr_days,
        steps_mean=steps_mean,
        steps_days=steps_days,
        last_health_date=last_health,
        last_sleep_date=last_sleep,
        caveats=tuple(caveats),
    )


def graph_context_pack(
    graph: EvidenceGraph,
    *,
    start: datetime,
    end: datetime,
    projects: Sequence[str] | None = None,
    weak_tags: bool = False,
    persist_weak_tags: bool = False,
    substrate_state: ContextPackSubstrateState | None = None,
) -> ContextPack:
    """Build a context pack from a prebuilt evidence graph."""
    evidence_pack = current_state_evidence_pack(
        start=start,
        end=end,
        projects=projects,
        include_github_frontier=graph.mode == "network",
        graph=graph,
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
        substrate_state=substrate_state or ContextPackSubstrateState(
            status="disabled",
            refresh_id=None,
            message="Context pack was built from a provided graph.",
        ),
        weak_tags=build_weak_tags(graph, persist=persist_weak_tags) if weak_tags else None,
        evidence_pack=evidence_pack,
        dataset_correlations=dataset_rows,
        claims=claims,
        projects=slices,
        salient_chains=chains,
        salient_anomalies=anomalies,
        readiness_forecast=readiness,
        caveats=_pack_caveats(evidence_pack=evidence_pack, graph=graph),
        physiology=_build_physiology(start=start, end=end),
    )


def _render_physiology(p: PhysiologySummary) -> str:
    def fmt(v: float | None, unit: str = "", prec: int = 1) -> str:
        return f"{v:.{prec}f}{unit}" if v is not None else "—"

    lines = [
        f"- Sleep: {fmt(p.sleep_hours_mean, 'h')} mean over {p.sleep_days}d "
        f"(score {fmt(p.sleep_score_mean)})",
        f"- Stress: {fmt(p.stress_mean)} ({p.stress_days}d) · "
        f"HRV rmssd {fmt(p.hrv_rmssd_mean)} ({p.hrv_days}d) · "
        f"resting HR {fmt(p.resting_hr_mean, ' bpm')} ({p.resting_hr_days}d)",
        f"- Steps: {fmt(p.steps_mean, '', 0)} mean ({p.steps_days}d)",
    ]
    for c in p.caveats:
        lines.append(f"- _caveat:_ {c.message}")
    return "\n".join(lines)


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
        f"- Evidence profile: `{pack.mode}`",
        f"- Generated: {pack.generated_at.isoformat(timespec='seconds')}",
        f"- Substrate graph: `{pack.substrate_state.status}` — {pack.substrate_state.message}",
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
            lines.append("**Temporal evidence chains:**")
            for c in pack.salient_chains:
                lines.append(f"- {c.date.isoformat()} — {c.summary} (confidence {c.confidence:.0%})")
            lines.append("")
    if pack.physiology is not None:
        lines.extend(
            [
                "## Operator Physiology",
                "",
                _render_physiology(pack.physiology),
                "",
            ]
        )
    if pack.weak_tags is not None:
        lines.extend(
            [
                "## Weak Evidence Tags",
                "",
                render_weak_tag_summary(pack.weak_tags),
                "",
            ]
        )
    work_event_section = _render_work_event_coverage(pack.graph)
    if work_event_section:
        lines.extend(["## AI Work-Event Coverage", "", work_event_section, ""])
    content_section = _render_content_metadata_coverage(start=pack.start.date(), end=pack.end.date())
    if content_section:
        lines.extend(["## Content Metadata Coverage", "", content_section, ""])
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
        caveats.append(EvidenceCaveat("github_ref", "partial", "GitHub refs are commit-subject references unless github_context lifecycle evidence is present."))
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
        #   low    -> "?kind"  (heuristic, single-feature, or sub-0.5 confidence)
        #   medium -> "kind"
        #   high   -> "kind"   (agreement or strong overlay features)
        # Missing tiers are rendered as uncertain rather than inferred from an
        # older confidence-only payload.
        tier = payload.get("kind_tier")
        if tier == "low":
            rendered_kind = f"?{kind}"
        elif tier in ("medium", "high"):
            rendered_kind = kind
        else:
            rendered_kind = f"?{kind}"
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


def _render_content_metadata_coverage(*, start: date, end: date) -> str:
    from ..materialization import ensure_materialized
    from ..sources.activity_content import iter_activity_content_days, iter_activity_title_usage

    ensure_materialized("activity_content", window=(start, end))
    days = list(iter_activity_content_days(start=start, end=end, ensure=False))
    if not days:
        return ""
    focused = sum(row.focused_seconds for row in days)
    matched = sum(row.matched_seconds for row in days)
    gpt = sum(row.gpt_matched_seconds for row in days)
    activity = _sum_seconds_buckets(row.activity_seconds for row in days)
    topics = _sum_seconds_buckets(row.topic_seconds for row in days)
    content = _sum_seconds_buckets(row.content_type_seconds for row in days)
    unmatched = [
        row
        for row in iter_activity_title_usage(start=start, end=end, ensure=False)
        if not row.matched
        and row.first_date is not None
        and row.last_date is not None
    ]
    unmatched.sort(key=lambda row: row.focused_seconds, reverse=True)
    lines = [
        f"- Days covered: {len(days)}",
        f"- Focused hours: {focused / 3600:.1f}",
        f"- Title metadata coverage: {(matched / focused):.1%}" if focused else "- Title metadata coverage: 0.0%",
        f"- GPT-classified coverage: {(gpt / focused):.1%}" if focused else "- GPT-classified coverage: 0.0%",
        f"- Top activities: {_format_seconds_buckets(activity)}",
        f"- Top topics: {_format_seconds_buckets(topics)}",
        f"- Top content types: {_format_seconds_buckets(content)}",
    ]
    if unmatched:
        lines.append("- Top unmatched titles:")
        for row in unmatched[:5]:
            title = row.normalized_title.replace("\n", " ")[:96]
            lines.append(f"  - {row.focused_seconds / 3600:.1f}h {row.app}: {title}")
    return "\n".join(lines)


def _sum_seconds_buckets(rows: Iterable[dict[str, float]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in rows:
        for key, value in row.items():
            totals[key] = totals.get(key, 0.0) + float(value)
    return totals


def _format_seconds_buckets(values: Mapping[str, float], *, limit: int = 5) -> str:
    if not values:
        return "-"
    return ", ".join(
        f"{label} {seconds / 3600:.1f}h"
        for label, seconds in sorted(values.items(), key=lambda item: item[1], reverse=True)[:limit]
    )


def _render_machine_analysis_artifacts(
    *,
    start: date,
    end: date,
    projects: Sequence[str],
) -> str:
    """Render compact machine-analysis summaries from materialized artifacts."""
    lines: list[str] = []
    artifacts = _load_machine_analysis_artifacts()
    if artifacts.missing:
        lines.append("- Missing machine analysis artifacts: " + ", ".join(artifacts.missing))
    if artifacts.malformed:
        lines.append("- Malformed machine analysis artifacts: " + ", ".join(artifacts.malformed))

    telemetry = artifacts.payloads.get("machine_telemetry_analysis.json")
    if telemetry is not None:
        coverage = _artifact_mapping(telemetry, "coverage")
        lines.append(
            "- Telemetry coverage: "
            f"samples={coverage.get('sample_count', 0)}; "
            f"span={coverage.get('first_observed_at', 'unknown')}..{coverage.get('last_observed_at', 'unknown')}; "
            f"hardware_regimes={len(_artifact_rows(telemetry, 'hardware_regimes'))}; "
            f"signals={len(_artifact_rows(telemetry, 'signals'))}"
        )

    episodes = _artifact_rows(artifacts.payloads.get("machine_episode_analysis.json"), "episodes")
    matching_episodes = [
        row for row in episodes
        if _row_overlaps(row, start=start, end=end, start_key="started_at", end_key="ended_at")
    ]
    if matching_episodes:
        lines.append(f"- Episodes in window: {len(matching_episodes)} ({_top_counts(row.get('kind') for row in matching_episodes)})")

    context = _artifact_rows(artifacts.payloads.get("machine_context_windows.json"), "windows")
    selected_projects = set(projects)
    matching_context = [
        row for row in context
        if _row_overlaps(row, start=start, end=end, start_key="started_at", end_key="ended_at")
        and (not selected_projects or selected_projects.intersection(_row_projects(row)))
    ]
    if matching_context:
        overlapped = sum(1 for row in matching_context if _row_int(row, "episode_count") > 0)
        lines.append(f"- Work windows with machine episodes: {overlapped}/{len(matching_context)}")

    states = artifacts.payloads.get("machine_work_state_windows.json")
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

    observations = artifacts.payloads.get("machine_work_observations.json")
    if observations is not None:
        daily = _artifact_rows(observations, "daily")
        sinex_check = _artifact_rows(observations, "sinex_check_daily")
        stages = _artifact_rows(observations, "stage_summaries")
        tests = _artifact_rows(observations, "test_summaries")
        if daily or stages or tests:
            lines.append(
                "- Work observations: "
                f"{len(daily)} daily groups; "
                f"sinex-check days={len(sinex_check)}; "
                f"stages={len(stages)}; tests={len(tests)}"
            )

    commands = artifacts.payloads.get("command_performance_windows.json")
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

    deltas = artifacts.payloads.get("machine_observational_deltas.json")
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

    candidates = artifacts.payloads.get("machine_attribution_candidates.json")
    if candidates is not None:
        candidate_rows = _artifact_rows(candidates, "candidates")
        if candidate_rows:
            frontier_count = candidates.get("pareto_frontier_count")
            if not isinstance(frontier_count, int):
                frontier_count = sum(1 for row in candidate_rows if row.get("pareto_frontier"))
            validation_counts = _top_counts(row.get("validation_status") for row in candidate_rows)
            family_counts = _top_counts(row.get("mechanism_family") for row in candidate_rows)
            top = sorted(
                candidate_rows,
                key=lambda row: _row_float(row, "priority_score"),
                reverse=True,
            )[:3]
            rendered = ", ".join(str(row.get("metric") or row.get("candidate_id")) for row in top)
            lines.append(
                "- Attribution candidates: "
                f"{len(candidate_rows)} non-causal candidates; "
                f"frontier={frontier_count}; "
                f"validation={validation_counts}; "
                f"families={family_counts}; "
                f"top={rendered}"
            )

    feature_frames = artifacts.payloads.get("machine_analysis_feature_frames.json")
    mining = artifacts.payloads.get("machine_mining.json")
    dataset_diagnostics = artifacts.payloads.get("machine_dataset_diagnostics.json")
    validation = artifacts.payloads.get("machine_validation_design.json")
    matched = artifacts.payloads.get("machine_matched_designs.json")
    comparisons = artifacts.payloads.get("machine_comparisons.json")
    if any(payload is not None for payload in (feature_frames, mining, dataset_diagnostics, validation, matched, comparisons)):
        feature_frame = _artifact_mapping(feature_frames, "frame")
        feature_audit = _artifact_mapping(dataset_diagnostics, "feature_audit")
        mining_audit = _artifact_mapping(dataset_diagnostics, "mining_audit")
        lines.append(
            "- Dataset mining infra: "
            f"feature_rows={_payload_count(feature_frame, 'row_count')}; "
            f"feature_status={feature_audit.get('status', 'unknown')}; "
            f"multiplicity={mining_audit.get('multiplicity_status', 'unknown')}; "
            f"cohorts={_payload_count(mining, 'cohort_count')}; "
            f"boundaries={_payload_count(validation, 'boundary_count')}; "
            f"matched_designs={_payload_count(matched, 'design_count')}; "
            f"contrasts={_payload_count(comparisons, 'contrast_count')}"
        )

    derivations = artifacts.payloads.get("machine_derivation_inventory.json")
    plans = artifacts.payloads.get("machine_benchmark_plans.json")
    bundle = artifacts.payloads.get("machine_benchmark_manifest_bundle.json")
    preflight = artifacts.payloads.get("machine_benchmark_preflight.json")
    handoff = artifacts.payloads.get("machine_benchmark_execution_handoff.json")
    manifests = artifacts.payloads.get("machine_experiment_manifest_diagnostics.json")
    if derivations is not None or plans is not None or bundle is not None or preflight is not None or manifests is not None:
        lines.append(
            "- Controlled benchmark infra: "
            f"derivations={_payload_count(derivations, 'ready_target_count')}; "
            f"ready_plans={_payload_count(plans, 'ready_plan_count')}; "
            f"run_templates={_payload_count(bundle, 'run_template_count')}; "
            f"preflight_ready={_payload_count(preflight, 'ready_run_count')}; "
            f"handoff_ready={_payload_count(handoff, 'ready_group_count')}/{_payload_count(handoff, 'handoff_count')}; "
            f"executed_valid={_payload_count(manifests, 'controlled_benchmark_valid_count')}; "
            f"legacy_observational={_payload_count(manifests, 'legacy_observational_count')}"
        )

    devshell = artifacts.payloads.get("devshell_performance.json")
    if devshell is not None:
        summaries = _artifact_rows(devshell, "summaries")
        if summaries:
            class_counts = {str(row.get("command_class")): row.get("command_count") for row in summaries if row.get("command_class")}
            lines.append(
                "- Devshell/Nix performance: "
                f"{devshell.get('command_count', 0)} commands; "
                f"classes={_format_mapping_counts(class_counts)}"
            )

    attribution = artifacts.payloads.get("machine_below_attribution.json")
    below_analysis = artifacts.payloads.get("machine_below_analysis.json")
    below_handoff = artifacts.payloads.get("machine_below_export_handoff.json")
    if below_analysis is not None:
        live_store = _artifact_mapping(below_analysis, "live_store")
        lines.append(
            "- Below analysis coverage: "
            f"bounded_windows={below_analysis.get('window_count', 0)}; "
            f"top_processes={below_analysis.get('top_process_count', 0)}; "
            f"top_cgroups={below_analysis.get('top_cgroup_count', 0)}; "
            f"live_store_indexes={live_store.get('index_count', 0)}"
        )
    if attribution is not None:
        bounded = attribution.get("attributed_episode_count")
        workload = attribution.get("workload_resource_attributed_pressure_episode_count")
        residual = attribution.get("residual_unattributed_pressure_episode_count")
        pressure = attribution.get("pressure_episode_count")
        if pressure is not None:
            lines.append(
                "- Process attribution: "
                f"bounded_below={bounded or 0}/{pressure}; "
                f"workload_resource={workload or 0}/{pressure}; "
                f"residual_unattributed={residual if residual is not None else attribution.get('unattributed_pressure_episode_count')}"
            )
    if below_handoff is not None:
        handoff_items = _artifact_rows(below_handoff, "items")
        if handoff_items:
            lines.append(
                "- Below export handoff: "
                f"{below_handoff.get('planned_window_count', len(handoff_items))} planned windows; "
                f"failed={below_handoff.get('failed_capture_count', 0)}; "
                f"kinds={_top_counts(row.get('episode_kind') for row in handoff_items)}; "
                f"root={below_handoff.get('root', 'unknown')}"
            )

    baselines = artifacts.payloads.get("machine_observational_baselines.json")
    if baselines is not None:
        caveats = _row_list(baselines, "caveats")
        hardware = _row_list(baselines, "by_hardware_regime")
        lines.append(f"- Observational baselines: {len(hardware)} hardware regimes; caveats: {len(caveats)}")

    claims = artifacts.payloads.get("machine_experiment_claims.json")
    if claims is not None:
        estimate_rows = _artifact_rows(claims, "effect_estimates")
        estimate_text = _machine_effect_estimate_summary(estimate_rows)
        lines.append(
            "- Experiment claim packs: "
            f"{claims.get('controlled_claim_count', 0)} controlled / "
            f"{claims.get('observational_claim_count', 0)} observational"
            f"{'; ' + estimate_text if estimate_text else ''}"
        )

    attribution_claims = artifacts.payloads.get("machine_attribution_claims.json")
    if attribution_claims is not None:
        by_support = attribution_claims.get("by_support_level")
        support_text = _format_mapping_counts(by_support) if isinstance(by_support, dict) else ""
        lines.append(
            "- Attribution claim ledger: "
            f"{attribution_claims.get('claim_count', 0)} claims"
            f"{'; ' + support_text if support_text else ''}"
        )

    mechanisms = artifacts.payloads.get("machine_mechanism_hypotheses.json")
    if mechanisms is not None:
        rows = _artifact_rows(mechanisms, "mechanisms")
        families = _top_counts(row.get("mechanism_family") for row in rows)
        lines.append(
            "- Mechanism hypotheses: "
            f"{mechanisms.get('mechanism_count', len(rows))} families"
            f"{'; ' + families if families else ''}"
        )

    instrumentation_gaps = artifacts.payloads.get("machine_instrumentation_gaps.json")
    if instrumentation_gaps is not None:
        by_source = instrumentation_gaps.get("by_missing_source")
        source_text = _format_mapping_counts(by_source) if isinstance(by_source, dict) else ""
        lines.append(
            "- Instrumentation gaps: "
            f"{instrumentation_gaps.get('gap_count', 0)} gaps"
            f"{'; ' + source_text if source_text else ''}"
        )

    negative_controls = artifacts.payloads.get("machine_negative_controls.json")
    if negative_controls is not None:
        by_status = negative_controls.get("by_status")
        status_text = _format_mapping_counts(by_status) if isinstance(by_status, dict) else ""
        lines.append(
            "- Negative controls: "
            f"{negative_controls.get('control_count', 0)} checks"
            f"{'; ' + status_text if status_text else ''}"
        )

    assumption_checks = artifacts.payloads.get("machine_assumption_checks.json")
    if assumption_checks is not None:
        by_status = assumption_checks.get("by_status")
        status_text = _format_mapping_counts(by_status) if isinstance(by_status, dict) else ""
        lines.append(
            "- Assumption checks: "
            f"{assumption_checks.get('check_count', 0)} checks"
            f"{'; ' + status_text if status_text else ''}"
        )

    calibration = artifacts.payloads.get("machine_calibration_fixtures.json")
    if calibration is not None:
        by_status = calibration.get("by_status")
        status_text = _format_mapping_counts(by_status) if isinstance(by_status, dict) else ""
        lines.append(
            "- Calibration fixtures: "
            f"{calibration.get('fixture_count', 0)} fixtures"
            f"{'; ' + status_text if status_text else ''}"
        )

    measurement = artifacts.payloads.get("machine_measurement_system.json")
    if measurement is not None:
        by_status = measurement.get("by_status")
        status_text = _format_mapping_counts(by_status) if isinstance(by_status, dict) else ""
        lines.append(
            "- Measurement system: "
            f"{measurement.get('check_count', 0)} checks"
            f"{'; ' + status_text if status_text else ''}"
        )

    support = artifacts.payloads.get("machine_support_assessment.json")
    if support is not None:
        assessments = _artifact_rows(support, "assessments")
        support_levels = _top_counts(row.get("support_level") for row in assessments)
        refusal_reasons = _top_counts(
            reason
            for row in assessments
            for reason in _row_list(row, "refusal_reasons")[:1]
        )
        next_actions = _top_counts(
            gap.get("next_action")
            for row in assessments
            for gap in _row_list(row, "instrumentation_gaps")
            if isinstance(gap, dict)
        )
        lines.append(
            "- Causal support gate: "
            f"{support.get('refusal_count', 0)}/{support.get('assessment_count', 0)} refused; "
            f"support={support_levels}; "
            f"top_refusal={refusal_reasons}; "
            f"next={next_actions}; "
            f"ready_plans={support.get('ready_plan_count', 0)}; "
            f"run_templates={support.get('run_template_count', 0)}; "
            f"controlled_claims={support.get('controlled_claim_count', 0)}"
        )

    readiness = artifacts.payloads.get("machine_analysis_readiness.json")
    if readiness is not None:
        dimensions = _artifact_rows(readiness, "dimensions")
        if dimensions:
            statuses = _top_counts(row.get("status") for row in dimensions)
            lines.append(f"- Machine analysis readiness: {statuses}")

    gaps = artifacts.payloads.get("machine_gap_summary.json")
    if gaps is not None:
        counts = _artifact_rows(gaps, "counts")
        regressions = _artifact_rows(gaps, "regressions")
        generated_for = _artifact_mapping(gaps, "generated_for")
        lines.append(
            "- Machine capture gaps: "
            f"counts={len(counts)}; regressions={len(regressions)}; "
            f"window={generated_for.get('window_start', 'unknown')}..{generated_for.get('window_end', 'unknown')}"
        )

    materialization_report = artifacts.payloads.get("machine_analysis_materialization_report.json")
    if materialization_report is not None:
        by_status = materialization_report.get("by_status")
        status_text = _format_mapping_counts(by_status) if isinstance(by_status, dict) else ""
        lines.append(
            "- Machine materialization report: "
            f"{materialization_report.get('step_count', 0)} steps"
            f"{'; ' + status_text if status_text else ''}"
        )

    if not lines:
        return ""
    lines.append("")
    lines.append("_Machine analysis is observational unless a manifest-backed controlled claim pack says otherwise._")
    return "\n".join(lines)


_MACHINE_ANALYSIS_ARTIFACTS = (
    "machine_episode_analysis.json",
    "machine_telemetry_analysis.json",
    "machine_below_analysis.json",
    "machine_context_windows.json",
    "machine_work_state_windows.json",
    "machine_work_observations.json",
    "machine_analysis_feature_frames.json",
    "machine_mining.json",
    "machine_dataset_diagnostics.json",
    "machine_validation_design.json",
    "machine_matched_designs.json",
    "machine_comparisons.json",
    "command_performance_windows.json",
    "machine_observational_deltas.json",
    "machine_attribution_candidates.json",
    "machine_derivation_inventory.json",
    "machine_benchmark_plans.json",
    "machine_benchmark_manifest_bundle.json",
    "machine_benchmark_preflight.json",
    "machine_benchmark_execution_handoff.json",
    "machine_experiment_manifest_diagnostics.json",
    "devshell_performance.json",
    "machine_below_attribution.json",
    "machine_below_export_handoff.json",
    "machine_observational_baselines.json",
    "machine_experiment_claims.json",
    "machine_attribution_claims.json",
    "machine_mechanism_hypotheses.json",
    "machine_instrumentation_gaps.json",
    "machine_negative_controls.json",
    "machine_assumption_checks.json",
    "machine_calibration_fixtures.json",
    "machine_measurement_system.json",
    "machine_support_assessment.json",
    "machine_gap_summary.json",
    "machine_analysis_readiness.json",
    "machine_analysis_materialization_report.json",
)


@dataclass(frozen=True)
class _MachineAnalysisArtifacts:
    payloads: Mapping[str, dict[str, object]]
    missing: tuple[str, ...]
    malformed: tuple[str, ...]


def _load_machine_analysis_artifacts() -> _MachineAnalysisArtifacts:
    from json import JSONDecodeError, loads

    from lynchpin.core.io import materialize_analysis_artifacts, resolve_analysis_path

    try:
        materialize_analysis_artifacts()
    except Exception:
        pass

    payloads: dict[str, dict[str, object]] = {}
    missing: list[str] = []
    malformed: list[str] = []
    for name in _MACHINE_ANALYSIS_ARTIFACTS:
        path = Path(resolve_analysis_path(name))
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            missing.append(name)
            continue
        except OSError:
            malformed.append(name)
            continue
        try:
            payload = loads(raw)
        except JSONDecodeError:
            malformed.append(name)
            continue
        if not isinstance(payload, dict):
            malformed.append(name)
            continue
        payloads[name] = payload
    return _MachineAnalysisArtifacts(
        payloads=payloads,
        missing=tuple(missing),
        malformed=tuple(malformed),
    )


def _artifact_rows(payload: object, key: str) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _artifact_mapping(payload: object, key: str) -> Mapping[str, object]:
    if not isinstance(payload, dict):
        return {}
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _payload_count(payload: object, key: str) -> int:
    if not isinstance(payload, dict):
        return 0
    value = payload.get(key)
    return int(value) if isinstance(value, (int, float, str)) and str(value).strip() else 0


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


def _machine_effect_estimate_summary(rows: list[dict[str, object]]) -> str:
    if not rows:
        return ""
    top = max(rows, key=lambda row: abs(_row_float(row, "delta")))
    pieces = [
        f"estimates={len(rows)}",
        f"top={top.get('run_group_id') or 'unknown'}",
        f"estimator={top.get('estimator') or 'unknown'}",
        f"delta={top.get('delta')}",
    ]
    if top.get("ci_low") is not None and top.get("ci_high") is not None:
        pieces.append(f"ci95=[{top.get('ci_low')}, {top.get('ci_high')}]")
    if top.get("p_value") is not None:
        pieces.append(f"p={top.get('p_value')}")
    if top.get("p_value_method"):
        pieces.append(f"p_method={top.get('p_value_method')}")
    return "; ".join(pieces)


def _pack_caveats(*, evidence_pack: CurrentStateEvidencePack, graph: EvidenceGraph) -> tuple[EvidenceCaveat, ...]:
    caveats = tuple(evidence_pack.source_readiness.caveats) + evidence_pack.movement.caveats + tuple(graph.caveats)
    return dedupe_caveats(caveats)


__all__ = [
    "ContextPack",
    "ProjectContextSlice",
    "context_pack",
    "graph_context_pack",
    "render_context_pack",
]
