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

    # Total active (non-recovery) hours across all months
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

    # Dominant work mode claim (exclude recovery — that's sleep/idle, not a mode)
    mode_hours: dict[str, float] = {}
    for month in months:
        for name, seconds in month.top_modes:
            if name != "recovery":
                mode_hours[name] = mode_hours.get(name, 0) + seconds / 3600

    if mode_hours:
        top_mode = max(mode_hours, key=mode_hours.get)
        mode_pct = mode_hours[top_mode] / total_hours
        if mode_pct > 0.2:
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

    # Rising project claim
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

    # Primary topic claim (>30% of topic hours)
    topic_hours: dict[str, float] = {}
    for month in months:
        for name, seconds in month.top_topics:
            topic_hours[name] = topic_hours.get(name, 0) + seconds / 3600
    if topic_hours:
        top_topic = max(topic_hours, key=topic_hours.get)
        topic_total = sum(topic_hours.values())
        if topic_total > 0:
            topic_pct = topic_hours[top_topic] / topic_total
            if topic_pct > 0.3 and topic_hours[top_topic] > 20:
                claims.append(
                    Claim(
                        statement=f"Dominant topic is {top_topic} ({topic_hours[top_topic]:.0f}h, {topic_pct:.0%} of topic hours)",
                        confidence=min(0.55 + topic_pct * 0.4, 0.9),
                        evidence_refs=("topic_hours",),
                        category="mode",
                    )
                )

    # Multi-project distribution claim (top 3 projects together cover most work)
    if project_hours and total_hours > 0:
        sorted_projects = sorted(project_hours.items(), key=lambda x: -x[1])
        top3_hours = sum(h for _, h in sorted_projects[:3])
        top3_pct = top3_hours / total_hours
        if 0.15 < top3_pct < 0.5 and len(sorted_projects) >= 3:
            top3_names = ", ".join(n for n, _ in sorted_projects[:3])
            claims.append(
                Claim(
                    statement=f"Distributed work across {top3_names} ({top3_pct:.0%} of active hours combined)",
                    confidence=0.7,
                    evidence_refs=tuple(f"month:{m.month}" for m in months[-2:]),
                    category="project",
                )
            )

    # Productivity density claim (commits per active day)
    if days:
        total_commits = sum(d.commit_count for d in days)
        total_active_days = sum(1 for d in days if d.active_seconds > 1800)
        if total_active_days > 0 and total_hours > 0:
            commits_per_day = total_commits / total_active_days
            if commits_per_day >= 5:
                claims.append(
                    Claim(
                        statement=f"High commit velocity ({commits_per_day:.1f} commits/active day, {total_commits} total)",
                        confidence=min(0.6 + commits_per_day * 0.02, 0.9),
                        evidence_refs=("commit_density",),
                        category="workflow",
                    )
                )

    # Schedule irregularity claim (high variance in daily active hours)
    if days and len(days) >= 14:
        active_hours_list = [d.active_seconds / 3600 for d in days if d.active_seconds > 0]
        if len(active_hours_list) >= 7:
            mean_h = sum(active_hours_list) / len(active_hours_list)
            variance = sum((h - mean_h) ** 2 for h in active_hours_list) / len(active_hours_list)
            std_h = variance ** 0.5
            if std_h > mean_h * 0.4:
                claims.append(
                    Claim(
                        statement=f"Irregular schedule (daily active hours: {mean_h:.1f}h avg ± {std_h:.1f}h std dev)",
                        confidence=0.75,
                        evidence_refs=("day_schedule",),
                        category="rhythm",
                    )
                )

    # Week-level claims
    if weeks and len(weeks) >= 3:
        weekly_hours = [w.active_seconds / 3600 for w in weeks]
        mean_weekly = sum(weekly_hours) / len(weekly_hours)

        # Peak week claim
        peak_week = max(weeks, key=lambda w: w.active_seconds)
        peak_hours = peak_week.active_seconds / 3600
        if peak_hours > mean_weekly * 1.5 and peak_hours >= 20:
            claims.append(
                Claim(
                    statement=f"Peak week was {peak_week.iso_week} ({peak_hours:.0f}h, {peak_hours / mean_weekly:.1f}× average)",
                    confidence=0.85,
                    evidence_refs=(f"week:{peak_week.iso_week}",),
                    category="rhythm",
                )
            )

        # Consistent weekly output claim
        if mean_weekly >= 20:
            std_w = (sum((h - mean_weekly) ** 2 for h in weekly_hours) / len(weekly_hours)) ** 0.5
            consistency = 1.0 - (std_w / mean_weekly)
            if consistency >= 0.7:
                claims.append(
                    Claim(
                        statement=f"Consistent weekly output ({mean_weekly:.0f}h/week avg, {consistency:.0%} consistency)",
                        confidence=min(0.6 + consistency * 0.3, 0.9),
                        evidence_refs=("weekly_hours",),
                        category="rhythm",
                    )
                )

        # Upward weekly trend claim
        if len(weeks) >= 4:
            first_half = weeks[: len(weeks) // 2]
            second_half = weeks[len(weeks) // 2 :]
            first_avg = sum(w.active_seconds for w in first_half) / 3600 / len(first_half)
            second_avg = sum(w.active_seconds for w in second_half) / 3600 / len(second_half)
            if second_avg > first_avg * 1.25 and second_avg >= 15:
                claims.append(
                    Claim(
                        statement=f"Rising weekly output ({first_avg:.0f}h early avg → {second_avg:.0f}h recent avg)",
                        confidence=0.75,
                        evidence_refs=("weekly_trend",),
                        category="rhythm",
                    )
                )
            elif second_avg < first_avg * 0.75 and first_avg >= 15:
                claims.append(
                    Claim(
                        statement=f"Declining weekly output ({first_avg:.0f}h early avg → {second_avg:.0f}h recent avg)",
                        confidence=0.75,
                        evidence_refs=("weekly_trend",),
                        category="rhythm",
                    )
                )

    claims.sort(key=lambda c: (-c.confidence, c.statement))
    return claims
