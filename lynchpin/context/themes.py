"""Theme detection from recurring activity patterns.

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
    """Scan top-3 projects/topics per month/week, track recurrence across periods.

    Monthly themes: project/topic appearing in 2+ months.
    Weekly themes: project/topic appearing in 3+ consecutive weeks (short-term focus).
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
    seen_names: set[tuple[str, str]] = set()  # (name, kind) — avoid duplicates

    for tracker, kind in [(project_months, "project"), (topic_months, "topic")]:
        for name, appearances in tracker.items():
            if len(appearances) < 2:
                continue
            total_hours = sum(h for _, h in appearances)
            sorted_app = sorted(appearances, key=lambda x: x[0])
            mid = len(sorted_app) // 2
            second_count = len(sorted_app) - mid

            if mid == 0:
                trend = "stable"
            else:
                first_avg = sum(h for _, h in sorted_app[:mid]) / mid
                second_avg = sum(h for _, h in sorted_app[mid:]) / second_count
                if second_avg > first_avg * 1.3:
                    trend = "rising"
                elif second_avg < first_avg * 0.7:
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
            seen_names.add((name, kind))

    # Weekly themes: detect 3+ consecutive ISO weeks with same project/topic in top-3
    if weeks and len(weeks) >= 3:
        sorted_weeks = sorted(weeks, key=lambda w: w.iso_week)

        for kind_attr, kind in [("top_projects", "project"), ("top_topics", "topic")]:
            # Build per-name: list of (week_idx, iso_week, hours)
            week_appearances: dict[str, list[tuple[int, str, float]]] = {}
            for i, week in enumerate(sorted_weeks):
                for name, seconds in getattr(week, kind_attr, ())[:3]:
                    week_appearances.setdefault(name, []).append((i, week.iso_week, seconds / 3600))

            for name, app_list in week_appearances.items():
                if len(app_list) < 3:
                    continue
                # Check for 3+ consecutive weeks (by index)
                indices = [idx for idx, _, _ in app_list]
                max_run = 1
                run = 1
                for j in range(1, len(indices)):
                    if indices[j] == indices[j - 1] + 1:
                        run += 1
                        max_run = max(max_run, run)
                    else:
                        run = 1
                if max_run < 3:
                    continue

                # Skip if already captured by monthly detection
                if (name, kind) in seen_names:
                    continue

                total_hours = sum(h for _, _, h in app_list)
                sorted_app = sorted(app_list, key=lambda x: x[1])
                mid = len(sorted_app) // 2
                second_count = len(sorted_app) - mid

                if mid == 0:
                    trend = "stable"
                else:
                    first_avg = sum(h for _, _, h in sorted_app[:mid]) / mid
                    second_avg = sum(h for _, _, h in sorted_app[mid:]) / second_count
                    if second_avg > first_avg * 1.3:
                        trend = "rising"
                    elif second_avg < first_avg * 0.7:
                        trend = "declining"
                    else:
                        trend = "stable"

                themes.append(
                    Theme(
                        name=name,
                        kind=kind,
                        total_hours=round(total_hours, 1),
                        month_count=len({wk for _, wk, _ in sorted_app}),
                        trend=trend,
                        first_seen=sorted_app[0][1],
                        last_seen=sorted_app[-1][1],
                    )
                )
                seen_names.add((name, kind))

    themes.sort(key=lambda t: (-t.total_hours, t.name))
    return themes
