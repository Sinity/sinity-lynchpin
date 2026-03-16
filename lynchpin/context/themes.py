"""Theme detection from trajectory activity patterns.

Scans top projects and topics across multiple months to identify recurring themes,
tracking their prevalence and trend (rising/stable/declining).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class Theme:
    name: str
    kind: str  # "project" or "topic"
    total_hours: float
    month_count: int
    trend: str  # "rising" | "stable" | "declining"
    first_seen: str
    last_seen: str


def detect_themes(
    months: Sequence,
    weeks: Optional[Sequence] = None,
) -> list[Theme]:
    """Scan top-3 projects/topics per month, track recurrence across 2+ months.

    Compute trend by comparing first-half vs second-half hours.
    """
    # Track per-name: list of (month_key, hours)
    project_months: dict[str, list[tuple[str, float]]] = {}
    topic_months: dict[str, list[tuple[str, float]]] = {}

    for month in months:
        for name, seconds in month.top_projects[:3]:
            project_months.setdefault(name, []).append((month.month, seconds / 3600))
        for name, seconds in month.top_topics[:3]:
            topic_months.setdefault(name, []).append((month.month, seconds / 3600))

    themes: list[Theme] = []

    for tracker, kind in [(project_months, "project"), (topic_months, "topic")]:
        for name, appearances in tracker.items():
            if len(appearances) < 2:
                continue
            total_hours = sum(h for _, h in appearances)
            sorted_app = sorted(appearances, key=lambda x: x[0])
            mid = len(sorted_app) // 2
            first_half = sum(h for _, h in sorted_app[:mid]) if mid > 0 else 0
            second_half = sum(h for _, h in sorted_app[mid:])

            if mid == 0:
                trend = "stable"
            elif second_half > first_half * 1.3:
                trend = "rising"
            elif second_half < first_half * 0.7:
                trend = "declining"
            else:
                trend = "stable"

            themes.append(
                Theme(
                    name=name,
                    kind=kind,
                    total_hours=round(total_hours, 1),
                    month_count=len(appearances),
                    trend=trend,
                    first_seen=sorted_app[0][0],
                    last_seen=sorted_app[-1][0],
                )
            )

    themes.sort(key=lambda t: (-t.total_hours, t.name))
    return themes
