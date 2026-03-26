"""Project arc analysis across derived period rollups.

Tracks individual project velocity trends, cost accumulation, and episode activity
to identify acceleration, stalling, or steady momentum patterns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class ProjectArc:
    project: str
    total_hours: float
    active_months: int
    velocity_trend: str  # "accelerating" | "steady" | "stalling"
    cost_usd: float
    active_episodes: int
    momentum: str  # "accelerating" | "steady" | "stalling"

    def to_dict(self) -> dict[str, object]:
        return {
            "project": self.project,
            "total_hours": self.total_hours,
            "active_months": self.active_months,
            "velocity_trend": self.velocity_trend,
            "cost_usd": round(self.cost_usd, 2),
            "active_episodes": self.active_episodes,
            "momentum": self.momentum,
        }


def build_project_arcs(
    months: Sequence,
    weeks: Optional[Sequence] = None,
    episodes: Optional[Sequence] = None,
) -> list[ProjectArc]:
    """Per top-5 project: velocity trend, cost trend, active episodes, momentum."""

    project_data: dict[str, dict] = {}

    for month in months:
        for name, seconds in month.top_projects:
            entry = project_data.setdefault(
                name,
                {
                    "hours": [],
                    "months": set(),
                    "cost": 0.0,
                    "episodes": 0,
                },
            )
            entry["hours"].append(seconds / 3600)
            entry["months"].add(month.month)

    # Count episodes per project
    if episodes:
        for ep in episodes:
            if ep.dominant_project and ep.dominant_project in project_data:
                project_data[ep.dominant_project]["episodes"] += 1

    # Accumulate cost from months with matching project
    for month in months:
        if month.chat_cost_usd:
            if month.dominant_project and month.dominant_project in project_data:
                project_data[month.dominant_project]["cost"] += month.chat_cost_usd

    # Per-project: accumulate weekly hours from most recent 4 weeks
    recent_week_hours: dict[str, list[float]] = {}
    if weeks:
        sorted_weeks = sorted(weeks, key=lambda w: w.iso_week)
        recent_4 = sorted_weeks[-4:]
        for week in recent_4:
            for name, seconds in week.top_projects[:5]:
                recent_week_hours.setdefault(name, []).append(seconds / 3600)

    # Sort by total hours, take top 5
    ranked = sorted(
        project_data.items(),
        key=lambda item: sum(item[1]["hours"]),
        reverse=True,
    )[:5]

    arcs: list[ProjectArc] = []
    for name, data in ranked:
        hours = data["hours"]
        total = sum(hours)
        mid = len(hours) // 2
        if mid > 0 and len(hours) > 1:
            first_avg = sum(hours[:mid]) / mid
            second_avg = sum(hours[mid:]) / (len(hours) - mid)
            if second_avg > first_avg * 1.3:
                trend = "accelerating"
            elif second_avg < first_avg * 0.7:
                trend = "stalling"
            else:
                trend = "steady"
        else:
            trend = "steady"

        # Weekly momentum: compare last 2 weeks vs prior 2 weeks
        week_hrs = recent_week_hours.get(name, [])
        if len(week_hrs) >= 3:
            half = len(week_hrs) // 2
            prior_avg = sum(week_hrs[:half]) / half
            recent_avg = sum(week_hrs[half:]) / (len(week_hrs) - half)
            if recent_avg > prior_avg * 1.25:
                momentum = "accelerating"
            elif recent_avg < prior_avg * 0.75:
                momentum = "stalling"
            else:
                momentum = "steady"
        else:
            momentum = trend  # fall back to velocity_trend when not enough weekly data

        arcs.append(
            ProjectArc(
                project=name,
                total_hours=round(total, 1),
                active_months=len(data["months"]),
                velocity_trend=trend,
                cost_usd=data["cost"],
                active_episodes=data["episodes"],
                momentum=momentum,
            )
        )

    return arcs
