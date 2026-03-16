"""Claims generation from trajectory data.

Rule-based inference to extract quantified statements about work patterns,
productivity, and behavioral trends from aggregated activity data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class Claim:
    statement: str
    confidence: float  # 0.5–0.95
    evidence_refs: tuple[str, ...]
    category: str  # "project", "mode", "rhythm", "workflow"


def generate_claims(
    months: Sequence,
    weeks: Optional[Sequence] = None,
    days: Optional[Sequence] = None,
) -> list[Claim]:
    """Rule-based claim generation from trajectory rollups."""
    claims: list[Claim] = []

    if not months:
        return claims

    # Total hours across all months
    total_hours = sum(m.active_seconds / 3600 for m in months)
    if total_hours == 0:
        return claims

    # Primary project claim (>40% hours)
    project_hours: dict[str, float] = {}
    for month in months:
        for name, seconds in month.top_projects:
            project_hours[name] = project_hours.get(name, 0) + seconds / 3600

    if project_hours:
        top_project = max(project_hours, key=project_hours.get)
        pct = project_hours[top_project] / total_hours
        if pct > 0.4:
            claims.append(
                Claim(
                    statement=f"Primary project is {top_project} ({pct:.0%} of active hours)",
                    confidence=min(0.5 + pct, 0.95),
                    evidence_refs=tuple(
                        f"month:{m.month}"
                        for m in months
                        if any(n == top_project for n, _ in m.top_projects)
                    ),
                    category="project",
                )
            )

    # Dominant mode claim
    mode_hours: dict[str, float] = {}
    for month in months:
        for name, seconds in month.top_modes:
            mode_hours[name] = mode_hours.get(name, 0) + seconds / 3600

    if mode_hours:
        top_mode = max(mode_hours, key=mode_hours.get)
        mode_pct = mode_hours[top_mode] / total_hours
        if mode_pct > 0.3:
            claims.append(
                Claim(
                    statement=f"Dominant mode is {top_mode} ({mode_pct:.0%} of active hours)",
                    confidence=min(0.5 + mode_pct * 0.5, 0.95),
                    evidence_refs=("all_months",),
                    category="mode",
                )
            )

    # Weekday pattern claim (if days provided)
    if days:
        weekday_hours = sum(
            d.active_seconds / 3600 for d in days if d.date.weekday() < 5
        )
        weekend_hours = sum(
            d.active_seconds / 3600 for d in days if d.date.weekday() >= 5
        )
        weekday_count = sum(1 for d in days if d.date.weekday() < 5)
        weekend_count = sum(1 for d in days if d.date.weekday() >= 5)
        if weekday_count > 0 and weekend_count > 0:
            weekday_avg = weekday_hours / weekday_count
            weekend_avg = weekend_hours / weekend_count
            if weekday_avg > weekend_avg * 1.5:
                claims.append(
                    Claim(
                        statement=f"Work peaks on weekdays ({weekday_avg:.1f}h/day vs {weekend_avg:.1f}h/day weekend)",
                        confidence=0.85,
                        evidence_refs=("day_pattern",),
                        category="rhythm",
                    )
                )
            elif weekend_avg > weekday_avg * 1.2:
                claims.append(
                    Claim(
                        statement=f"Weekend-heavy work pattern ({weekend_avg:.1f}h/day vs {weekday_avg:.1f}h/day weekday)",
                        confidence=0.8,
                        evidence_refs=("day_pattern",),
                        category="rhythm",
                    )
                )

    # Chat-heavy workflow claim
    total_sessions = sum(m.chat_session_count for m in months)
    active_days_total = sum(m.active_days for m in months)
    if active_days_total > 0 and total_sessions / active_days_total > 2:
        ratio = total_sessions / active_days_total
        claims.append(
            Claim(
                statement=f"Chat-heavy workflow ({ratio:.1f} sessions/active day)",
                confidence=min(0.6 + ratio * 0.05, 0.9),
                evidence_refs=("chat_sessions",),
                category="workflow",
            )
        )

    # Rising topic/project claim
    if len(months) >= 3:
        recent = months[-2:]
        earlier = months[:-2]
        recent_projects: dict[str, float] = {}
        earlier_projects: dict[str, float] = {}
        for m in recent:
            for name, seconds in m.top_projects[:3]:
                recent_projects[name] = recent_projects.get(name, 0) + seconds / 3600
        for m in earlier:
            for name, seconds in m.top_projects[:3]:
                earlier_projects[name] = earlier_projects.get(name, 0) + seconds / 3600
        for name, hours in recent_projects.items():
            if hours > 10 and earlier_projects.get(name, 0) < hours * 0.3:
                claims.append(
                    Claim(
                        statement=f"{name} is rising ({hours:.0f}h recent vs {earlier_projects.get(name, 0):.0f}h earlier)",
                        confidence=0.75,
                        evidence_refs=tuple(f"month:{m.month}" for m in recent),
                        category="project",
                    )
                )

    claims.sort(key=lambda c: (-c.confidence, c.statement))
    return claims
