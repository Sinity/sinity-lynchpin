"""Multi-dimensional movement summaries for current-state analysis."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from ..core.evidence import EvidenceCaveat
from .work_correlation import CorrelatedWorkDay, work_day_correlations


@dataclass(frozen=True)
class ProjectMovement:
    project: str
    active_days: int
    cross_source_days: int
    commits: int
    ai_sessions: int
    raw_log_entries: int
    focus_hours: float
    shell_commands: int
    github_refs: int
    lifecycle_counts: dict[str, int]
    sources: tuple[str, ...]
    caveats: tuple[EvidenceCaveat, ...]
    # Arc B.2: aggregate AI work-event kinds across the window. Raw counts
    # come from CorrelatedWorkDay.ai_kind_breakdown; weighted scores from
    # ai_kind_weighted (Arc K's tier weights × session message-count cap).
    kind_breakdown: dict[str, int] = field(default_factory=dict)
    kind_breakdown_weighted: dict[str, float] = field(default_factory=dict)

    @property
    def corroboration_ratio(self) -> float:
        if self.active_days == 0:
            return 0.0
        return round(self.cross_source_days / self.active_days, 3)

    @property
    def dominant_kind(self) -> str | None:
        """Top kind by weighted score; tiebreak by raw count."""
        if not self.kind_breakdown_weighted:
            return None
        return max(
            self.kind_breakdown_weighted,
            key=lambda kind: (
                self.kind_breakdown_weighted[kind],
                self.kind_breakdown.get(kind, 0),
            ),
        )


@dataclass(frozen=True)
class MovementSummary:
    start: date
    end: date
    projects: tuple[ProjectMovement, ...]
    caveats: tuple[EvidenceCaveat, ...]


def movement_summary(
    *,
    start: date,
    end: date,
    rows: Sequence[CorrelatedWorkDay] | None = None,
    include_github_context: bool = False,
) -> MovementSummary:
    """Summarize movement without treating raw counts as standalone velocity."""
    correlation_rows = tuple(rows) if rows is not None else work_day_correlations(
        start=start,
        end=end,
        include_github_context=include_github_context,
    )
    by_project: dict[str, list[CorrelatedWorkDay]] = defaultdict(list)
    for row in correlation_rows:
        by_project[row.project].append(row)

    projects = tuple(
        _project_movement(project, rows)
        for project, rows in sorted(by_project.items(), key=lambda item: item[0])
    )
    caveats = (
        EvidenceCaveat(
            "movement",
            "partial",
            "Raw commit, issue, chat, focus, and shell counts are separate evidence dimensions; do not collapse them into one velocity scalar.",
        ),
    )
    return MovementSummary(start=start, end=end, projects=projects, caveats=caveats)


def render_movement_summary(summary: MovementSummary, *, limit: int = 16) -> str:
    """Render project movement in Markdown."""
    ordered = sorted(
        summary.projects,
        key=lambda item: (
            item.cross_source_days,
            item.commits,
            item.ai_sessions,
            item.focus_hours,
            item.shell_commands,
        ),
        reverse=True,
    )[:limit]
    lines = [
        "| Project | Days | Cross-Source | Commits | AI Sessions | Kinds | Raw Log | Focus h | Shell cmds | GitHub refs | Sources | Caveats |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---|",
    ]
    for project in ordered:
        caveats = "<br>".join(c.message.replace("|", "\\|") for c in project.caveats)
        lines.append(
            "| {project} | {days} | {cross} ({ratio:.0%}) | {commits} | {ai} | {kinds} | {raw} | {focus:.2f} | {shell} | {github} | {sources} | {caveats} |".format(
                project=project.project,
                days=project.active_days,
                cross=project.cross_source_days,
                ratio=project.corroboration_ratio,
                commits=project.commits,
                ai=project.ai_sessions,
                kinds=_render_kinds(project),
                raw=project.raw_log_entries,
                focus=project.focus_hours,
                shell=project.shell_commands,
                github=project.github_refs,
                sources=", ".join(project.sources),
                caveats=caveats,
            )
        )
    if not ordered:
        lines.append("|  | 0 | 0 | 0 | 0 |  | 0 | 0.00 | 0 | 0 |  | no correlated movement rows |")
    return "\n".join(lines)


def _render_kinds(project: ProjectMovement, *, top: int = 3) -> str:
    """Render the top-N kinds as `kind×N` items, ranked by weighted score."""
    if not project.kind_breakdown:
        return ""
    breakdown = project.kind_breakdown
    weighted = project.kind_breakdown_weighted
    ranked = sorted(
        breakdown,
        key=lambda kind: (weighted.get(kind, 0.0), breakdown[kind]),
        reverse=True,
    )[:top]
    return ", ".join(f"{kind[:6]}×{breakdown[kind]}" for kind in ranked)


def _project_movement(project: str, rows: Sequence[CorrelatedWorkDay]) -> ProjectMovement:
    lifecycle_counts: Counter[str] = Counter()
    sources: set[str] = set()
    caveats: list[EvidenceCaveat] = []
    kind_breakdown: Counter[str] = Counter()
    kind_weighted: dict[str, float] = {}
    for row in rows:
        lifecycle_counts.update(row.github_lifecycles)
        sources.update(row.sources)
        for kind, count in row.ai_kind_breakdown:
            kind_breakdown[kind] += count
        for kind, weight in row.ai_kind_weighted:
            kind_weighted[kind] = kind_weighted.get(kind, 0.0) + weight

    commits = sum(row.commit_count for row in rows)
    github_refs = sum(len(row.github_refs) for row in rows)
    if commits:
        caveats.append(EvidenceCaveat("git", "partial", "Commit count varies widely by commit scope; interpret with subjects and cross-source support."))
    if github_refs:
        caveats.append(EvidenceCaveat("github", "partial", "GitHub refs may include folded, retired, tracking, or superseded work; inspect lifecycle counts."))
    if any(row.ai_session_count for row in rows):
        caveats.append(EvidenceCaveat("polylogue", "partial", "AI sessions indicate assistance intensity, not necessarily independent work units."))
    if any(row.focus_minutes for row in rows):
        caveats.append(EvidenceCaveat("activitywatch", "partial", "Focus attribution is project/date-level and may miss terminal/editor windows without project metadata."))
    if kind_breakdown:
        caveats.append(EvidenceCaveat(
            "polylogue",
            "partial",
            "AI kind breakdown is heuristic (per-event labels with low tiers down-weighted); do not collapse kinds into a single 'AI velocity' scalar.",
        ))

    return ProjectMovement(
        project=project,
        active_days=len({row.date for row in rows}),
        cross_source_days=sum(1 for row in rows if row.has_cross_source_support),
        commits=commits,
        ai_sessions=sum(row.ai_session_count for row in rows),
        raw_log_entries=sum(row.raw_log_count for row in rows),
        focus_hours=round(sum(row.focus_minutes for row in rows) / 60.0, 2),
        shell_commands=sum(row.shell_command_count for row in rows),
        github_refs=github_refs,
        lifecycle_counts=dict(sorted(lifecycle_counts.items())),
        sources=tuple(sorted(sources)),
        caveats=tuple(caveats),
        kind_breakdown=dict(kind_breakdown),
        kind_breakdown_weighted={k: round(v, 2) for k, v in kind_weighted.items()},
    )


__all__ = [
    "MovementSummary",
    "ProjectMovement",
    "movement_summary",
    "render_movement_summary",
]
