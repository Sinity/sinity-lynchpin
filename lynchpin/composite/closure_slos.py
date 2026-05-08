"""Frontier closure SLOs (M.9).

Builds on Arc C.2 ``IssueClosureChain`` to compute service-level metrics
across the closure stream:

  - per-project median / p75 / p90 open→complete duration (from chains
    with status ``complete`` and both opened_at + closed_at present)
  - count of stale ``tracking/horizon`` issues exceeding a threshold
    (opened ≥ N days ago without progress) — surfaced as the "spine
    that's gone cold" signal
  - count of ``broken`` and ``orphaned`` chains, since they're SLO
    violations even more than slow ones

Intent is descriptive, not prescriptive. The closure stream is
heuristic-linked (PR titles substring-match issue numbers), so the SLO
numbers are best read as relative-shape signals rather than precise
durations.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Sequence

from .issue_closure_chain import IssueClosureChain


_STALE_TRACKING_DAYS = 90  # "horizon" issue gone cold


@dataclass(frozen=True)
class ProjectClosureSLO:
    project: str
    completed_count: int
    median_days_to_close: float | None
    p75_days_to_close: float | None
    p90_days_to_close: float | None
    broken_count: int
    orphaned_count: int
    partial_count: int
    stale_tracking_count: int


@dataclass(frozen=True)
class ClosureSLOs:
    generated_at: datetime
    reference_date: date
    projects: tuple[ProjectClosureSLO, ...]
    overall_completed: int
    overall_broken: int
    overall_orphaned: int
    overall_stale_tracking: int


def compute_closure_slos(
    chains: Sequence[IssueClosureChain],
    *,
    reference: date | None = None,
    stale_tracking_days: int = _STALE_TRACKING_DAYS,
) -> ClosureSLOs:
    """Aggregate closure-chain rows into per-project SLO rows."""
    ref = reference or datetime.now(timezone.utc).date()

    by_project: dict[str, list[IssueClosureChain]] = {}
    for chain in chains:
        by_project.setdefault(chain.project, []).append(chain)

    projects: list[ProjectClosureSLO] = []
    overall_completed = 0
    overall_broken = 0
    overall_orphaned = 0
    overall_stale = 0

    for project in sorted(by_project):
        project_chains = by_project[project]
        completed = [
            chain for chain in project_chains
            if chain.closure_status == "complete"
            and chain.opened_at is not None
            and chain.closed_at is not None
        ]
        broken = sum(1 for chain in project_chains if chain.closure_status == "broken")
        orphaned = sum(1 for chain in project_chains if chain.closure_status == "orphaned")
        partial = sum(1 for chain in project_chains if chain.closure_status == "partial")

        durations_days = [
            (chain.closed_at.date() - chain.opened_at.date()).days
            for chain in completed
            if chain.closed_at is not None and chain.opened_at is not None
        ]
        durations_days = [d for d in durations_days if d >= 0]

        median = statistics.median(durations_days) if durations_days else None
        p75 = _percentile(durations_days, 0.75)
        p90 = _percentile(durations_days, 0.90)

        # Stale tracking: open issue (which we represent as missing closed_at)
        # in the lifecycle bucket "tracking_or_horizon" — chain.issue_lifecycle
        # carries the GitHub frontier classification.
        stale = sum(
            1 for chain in project_chains
            if chain.issue_lifecycle == "tracking_or_horizon"
            and chain.closed_at is None
            and chain.opened_at is not None
            and (ref - chain.opened_at.date()).days >= stale_tracking_days
        )

        projects.append(ProjectClosureSLO(
            project=project,
            completed_count=len(completed),
            median_days_to_close=median,
            p75_days_to_close=p75,
            p90_days_to_close=p90,
            broken_count=broken,
            orphaned_count=orphaned,
            partial_count=partial,
            stale_tracking_count=stale,
        ))
        overall_completed += len(completed)
        overall_broken += broken
        overall_orphaned += orphaned
        overall_stale += stale

    return ClosureSLOs(
        generated_at=datetime.now(timezone.utc),
        reference_date=ref,
        projects=tuple(projects),
        overall_completed=overall_completed,
        overall_broken=overall_broken,
        overall_orphaned=overall_orphaned,
        overall_stale_tracking=overall_stale,
    )


def render_closure_slos(slos: ClosureSLOs) -> str:
    """Render SLOs as a Markdown table prioritized by SLO violation density."""
    if not slos.projects:
        return "_No closure chains in window for SLO analysis._"

    def _violation_score(row: ProjectClosureSLO) -> int:
        return row.broken_count * 3 + row.orphaned_count * 2 + row.stale_tracking_count

    ordered = sorted(slos.projects, key=lambda r: (-_violation_score(r), r.project))
    lines = [
        f"_Reference {slos.reference_date.isoformat()} • "
        f"{slos.overall_completed} completed, {slos.overall_broken} broken, "
        f"{slos.overall_orphaned} orphaned, {slos.overall_stale_tracking} stale tracking_",
        "",
        "| Project | Completed | Median d | p75 d | p90 d | Broken | Orphaned | Partial | Stale tracking |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in ordered:
        median = _fmt_days(row.median_days_to_close)
        p75 = _fmt_days(row.p75_days_to_close)
        p90 = _fmt_days(row.p90_days_to_close)
        lines.append(
            f"| {row.project} | {row.completed_count} | {median} | {p75} | {p90} | "
            f"{_warn(row.broken_count)} | {_warn(row.orphaned_count)} | "
            f"{row.partial_count} | {_warn(row.stale_tracking_count)} |"
        )
    return "\n".join(lines)


def _percentile(values: Sequence[int], pct: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    idx = max(0, min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1)))))
    return float(sorted_values[idx])


def _fmt_days(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}"


def _warn(value: int) -> str:
    if value == 0:
        return "0"
    return f"**{value}**"


__all__ = [
    "ClosureSLOs",
    "ProjectClosureSLO",
    "compute_closure_slos",
    "render_closure_slos",
]
