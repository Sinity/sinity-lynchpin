"""Narrative consumer over context packs.

Not an NLG system. A deterministic structured-stitching engine that groups
NarrativeMoment records by time/project, resolves supporting evidence from
the graph, and produces sectioned NarrativeReport output. Deterministic only
— no LLM calls. Section summaries are template-stitched from evidence.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Literal

from ..core.parse import as_local
from ..core.serialization import jsonable
from .context_pack import ContextPack, context_pack
from .weak_tags import NarrativeMoment

SectionType = Literal[
    "temporal_evidence_chain",
    "daily_summary",
    "periodic_overview",
    "project_retrospective",
    "cross_source_insight",
    "notable_moment",
    "caveats_and_gaps",
]


@dataclass(frozen=True)
class NarrativeSection:
    section_type: SectionType
    title: str
    date: date | None
    project: str | None
    moments: tuple[NarrativeMoment, ...]
    supporting_claims: tuple[str, ...]
    summary: str
    score: float


@dataclass(frozen=True)
class NarrativeReport:
    start: date
    end: date
    generated_at: datetime
    sections: tuple[NarrativeSection, ...]
    total_moments: int
    moment_count: int
    project_count: int
    top_labels: tuple[str, ...]
    caveats: tuple[str, ...]


def build_narrative(
    pack: ContextPack,
    *,
    moment_limit: int = 24,
    min_score: float = 1.5,
) -> NarrativeReport:
    """Build a structured narrative report from a context pack.

    Groups NarrativeMoment records by date and project, resolves supporting
    evidence from the graph, and produces sectioned output. Deterministic:
    no LLM calls — summaries are template-stitched from moment labels,
    source counts, and claim summaries.
    """
    enrichment = pack.weak_tags
    moments: tuple[NarrativeMoment, ...] = ()
    if enrichment is not None:
        moments = tuple(
            m for m in sorted(enrichment.moments, key=lambda m: -m.score)
            if m.score >= min_score
        )[:moment_limit]

    claims_by_project = _index_claims(pack.claims)
    sections: list[NarrativeSection] = []
    caveats: list[str] = list(_pack_caveats(pack))

    if not moments:
        sections.append(NarrativeSection(
            section_type="caveats_and_gaps",
            title="No Narrative Moments",
            date=None,
            project=None,
            moments=(),
            supporting_claims=(),
            summary="No narrative moments met the score threshold in this window. "
                    "This may indicate a quiet period, degraded source data, or "
                    "a window too narrow for statistical enrichment to surface patterns.",
            score=0.0,
        ))
        return NarrativeReport(
            start=pack.start.date(),
            end=pack.end.date(),
            generated_at=datetime.now(timezone.utc),
            sections=tuple(sections),
            total_moments=len(enrichment.moments) if enrichment else 0,
            moment_count=0,
            project_count=0,
            top_labels=(),
            caveats=tuple(caveats),
        )

    daily = _group_by_date(moments)
    periodic = _periodic_patterns(moments)
    by_project = _group_by_project(moments)
    cross_source = [m for m in moments if len(m.labels) >= 2]
    notable = [m for m in moments if m.score >= 3.0]

    for d, day_moments in sorted(daily.items())[:7]:
        sections.append(_daily_section(d, day_moments, claims_by_project))

    if periodic:
        sections.append(_periodic_section(periodic, claims_by_project))

    for project, proj_moments in sorted(by_project.items(), key=lambda x: -len(x[1]))[:5]:
        sections.append(_project_section(project, proj_moments, claims_by_project))

    if cross_source:
        sections.append(_cross_source_section(cross_source, claims_by_project))

    for moment in notable[:5]:
        sections.append(_notable_section(moment, claims_by_project))

    try:
        from .causal_chains import detect_chains
        chains = detect_chains(pack.graph.nodes, max_gap_minutes=60)
        for chain in chains[:5]:
            sections.append(_chain_section(chain))
    except Exception:
        pass

    project_set = {m.project for m in moments if m.project}
    label_counter: dict[str, int] = {}
    for m in moments:
        for label in m.labels:
            label_counter[label] = label_counter.get(label, 0) + 1
    top_labels = tuple(
        label for label, _ in sorted(label_counter.items(), key=lambda x: -x[1])[:8]
    )

    return NarrativeReport(
        start=pack.start.date(),
        end=pack.end.date(),
        generated_at=datetime.now(timezone.utc),
        sections=tuple(sections),
        total_moments=len(enrichment.moments) if enrichment else 0,
        moment_count=len(moments),
        project_count=len(project_set),
        top_labels=top_labels,
        caveats=tuple(caveats),
    )


def narrative_for_period(
    *,
    start: date,
    end: date,
    projects: Sequence[str] | None = None,
    moment_limit: int = 24,
    min_score: float = 1.5,
) -> NarrativeReport:
    """Build context pack + weak evidence tags + narrative in one call."""
    start_dt = as_local(datetime.combine(start, time.min))
    end_dt = as_local(datetime.combine(end, time.max))
    pack = context_pack(
        start=start_dt,
        end=end_dt,
        projects=projects,
        weak_tags=True,
        exclude_analysis_artifacts=(
            "current_state_context_pack.json",
            "current_state_context_pack.md",
            "current_state_narrative.json",
            "current_state_narrative.md",
        ),
    )
    return build_narrative(pack, moment_limit=moment_limit, min_score=min_score)


def render_narrative_markdown(report: NarrativeReport) -> str:
    """Render a narrative report as structured markdown."""
    lines = [
        f"# Narrative Report ({report.start} → {report.end})",
        "",
        f"- Generated: {report.generated_at.isoformat(timespec='seconds')}",
        f"- Narrative moments: {report.moment_count} surfaced / {report.total_moments} total",
        f"- Projects: {report.project_count}",
        f"- Top labels: {', '.join(report.top_labels[:6])}" if report.top_labels else "",
        "",
    ]
    lines = [line for line in lines if line != ""]

    for section in report.sections:
        lines.append("")
        lines.append(f"## {section.title}")
        lines.append("")
        lines.append(section.summary)
        lines.append("")

        if section.supporting_claims:
            lines.append("**Supporting claims:** " + "; ".join(section.supporting_claims[:5]))
            lines.append("")

        for moment in section.moments[:8]:
            project_tag = f" [{moment.project}]" if moment.project else ""
            lines.append(f"- **{moment.title}**{project_tag} (score: {moment.score:.1f})")
            if moment.summary:
                lines.append(f"  {moment.summary}")
            if moment.labels:
                lines.append(f"  labels: {', '.join(moment.labels[:5])}")

        lines.append("")

    if report.caveats:
        lines.append("## Caveats")
        lines.append("")
        for caveat in report.caveats:
            lines.append(f"- {caveat}")
        lines.append("")

    return "\n".join(lines)


def render_narrative_json(report: NarrativeReport) -> dict[str, Any]:
    """JSON-serializable form for programmatic consumers."""
    result = jsonable(report)
    return result if isinstance(result, dict) else {"error": "jsonable returned non-dict"}


def narrate(
    *,
    start: date,
    end: date,
    projects: Sequence[str] | None = None,
    moment_limit: int = 24,
    min_score: float = 1.5,
    out: str | None = None,
    json_out: str | None = None,
) -> NarrativeReport:
    """Build and optionally persist a narrative report."""
    report = narrative_for_period(
        start=start, end=end, projects=projects,
        moment_limit=moment_limit, min_score=min_score,
    )
    if out:
        from lynchpin.core.io import save_text
        save_text(out, render_narrative_markdown(report) + "\n")
    if json_out:
        from lynchpin.core.io import save_json
        save_json(json_out, render_narrative_json(report), sort_keys=True)
    return report


def _index_claims(claims: Sequence[Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for claim in claims:
        project = getattr(claim, "project", None) or ""
        summary = getattr(claim, "summary", "") or ""
        if project and summary:
            result[project].append(summary)
    return dict(result)


def _group_by_date(moments: Sequence[NarrativeMoment]) -> dict[date, list[NarrativeMoment]]:
    grouped: dict[date, list[NarrativeMoment]] = defaultdict(list)
    for m in moments:
        grouped[m.date].append(m)
    return dict(grouped)


def _group_by_project(moments: Sequence[NarrativeMoment]) -> dict[str, list[NarrativeMoment]]:
    grouped: dict[str, list[NarrativeMoment]] = defaultdict(list)
    for m in moments:
        if m.project:
            grouped[m.project].append(m)
    return dict(grouped)


def _periodic_patterns(moments: Sequence[NarrativeMoment]) -> list[str]:
    patterns: list[str] = []
    dates = sorted({m.date for m in moments})
    if len(dates) < 2:
        return patterns

    labels_by_date: dict[date, set[str]] = {}
    for m in moments:
        labels_by_date.setdefault(m.date, set()).update(m.labels)

    recurring: dict[str, int] = {}
    for label_sets in labels_by_date.values():
        for label in label_sets:
            recurring[label] = recurring.get(label, 0) + 1
    threshold = max(2, len(dates) // 2)
    for label, count in recurring.items():
        if count >= threshold:
            patterns.append(f"'{label}' appeared on {count}/{len(dates)} days")

    return patterns[:5]


def _daily_section(
    d: date,
    day_moments: list[NarrativeMoment],
    claims_by_project: dict[str, list[str]],
) -> NarrativeSection:
    projects = sorted({m.project for m in day_moments if m.project})
    labels = sorted({label for m in day_moments for label in m.labels})
    total_score = sum(m.score for m in day_moments)
    supporting: list[str] = []
    for p in projects:
        supporting.extend(claims_by_project.get(p, [])[:2])

    summary_parts = [
        f"{len(day_moments)} notable events across {len(projects)} project(s).",
    ]
    if labels:
        summary_parts.append(f"Activity signals: {', '.join(labels[:6])}.")
    if projects:
        summary_parts.append(f"Projects: {', '.join(projects[:5])}.")

    return NarrativeSection(
        section_type="daily_summary",
        title=f"Daily Summary — {d.isoformat()}",
        date=d,
        project=None,
        moments=tuple(day_moments),
        supporting_claims=tuple(supporting),
        summary=" ".join(summary_parts),
        score=round(total_score, 1),
    )


def _periodic_section(
    patterns: list[str],
    claims_by_project: dict[str, list[str]],
) -> NarrativeSection:
    return NarrativeSection(
        section_type="periodic_overview",
        title="Periodic Patterns",
        date=None,
        project=None,
        moments=(),
        supporting_claims=(),
        summary="Recurring signals across the window: " + "; ".join(patterns) + ".",
        score=0.0,
    )


def _project_section(
    project: str,
    proj_moments: list[NarrativeMoment],
    claims_by_project: dict[str, list[str]],
) -> NarrativeSection:
    labels = sorted({label for m in proj_moments for label in m.labels})
    total_score = sum(m.score for m in proj_moments)
    dates = sorted({m.date for m in proj_moments})
    claims = claims_by_project.get(project, [])
    date_range = f"{dates[0]} to {dates[-1]}" if len(dates) > 1 else str(dates[0])

    summary_parts = [
        f"{len(proj_moments)} moments across {len(dates)} day(s) ({date_range}).",
    ]
    if labels:
        summary_parts.append(f"Signals: {', '.join(labels[:6])}.")
    if claims:
        top_claim = claims[0]
        if len(top_claim) > 120:
            top_claim = top_claim[:120] + "..."
        summary_parts.append(f"Top claim: {top_claim}")

    return NarrativeSection(
        section_type="project_retrospective",
        title=f"Project Retrospective — {project}",
        date=None,
        project=project,
        moments=tuple(proj_moments),
        supporting_claims=tuple(claims[:5]),
        summary=" ".join(summary_parts),
        score=round(total_score, 1),
    )


def _cross_source_section(
    moments: list[NarrativeMoment],
    claims_by_project: dict[str, list[str]],
) -> NarrativeSection:
    sources: set[str] = set()
    for m in moments:
        sources.update(m.labels)
    projects = sorted({m.project for m in moments if m.project})
    supporting: list[str] = []
    for p in projects:
        supporting.extend(claims_by_project.get(p, [])[:2])

    return NarrativeSection(
        section_type="cross_source_insight",
        title="Cross-Source Insights",
        date=None,
        project=None,
        moments=tuple(moments),
        supporting_claims=tuple(supporting),
        summary=(
            f"{len(moments)} moments with evidence from multiple sources. "
            f"Sources involved: {', '.join(sorted(sources)[:8])}. "
            f"Cross-source moments are stronger evidence because they are "
            f"corroborated by independent data streams."
        ),
        score=sum(m.score for m in moments),
    )


def _notable_section(
    moment: NarrativeMoment,
    claims_by_project: dict[str, list[str]],
) -> NarrativeSection:
    project = moment.project or ""
    claims = claims_by_project.get(project, [])[:3]

    return NarrativeSection(
        section_type="notable_moment",
        title=f"Notable: {moment.title}",
        date=moment.date,
        project=moment.project,
        moments=(moment,),
        supporting_claims=tuple(claims),
        summary=moment.summary,
        score=moment.score,
    )


def _chain_section(chain) -> NarrativeSection:
    gaps = ", ".join(f"{g:.0f}m" for g in chain.time_gaps_minutes)
    return NarrativeSection(
        section_type="temporal_evidence_chain",
        title=f"Temporal Evidence Chain: {chain.summary}",
        date=chain.date,
        project=None,
        moments=(),
        supporting_claims=(),
        summary=(
            f"{' → '.join(chain.node_kinds)} within {gaps} "
            f"(confidence: {chain.confidence:.0%})"
        ),
        score=chain.confidence * 3.0,
    )


def _pack_caveats(pack: ContextPack) -> tuple[str, ...]:
    caveats: list[str] = []
    for c in pack.caveats:
        caveats.append(f"[{c.source}] {c.message}")
    if pack.weak_tags is None:
        caveats.append(
            "Weak evidence tags not available — narrative is built from "
            "claims and evidence only, without scored narrative moments."
        )
    return tuple(caveats)


__all__ = [
    "NarrativeReport",
    "NarrativeSection",
    "build_narrative",
    "narrate",
    "narrative_for_period",
    "render_narrative_json",
    "render_narrative_markdown",
]
