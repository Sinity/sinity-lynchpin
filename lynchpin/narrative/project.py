"""Project-centric narratives — per-project focus analysis.

Groups spans by dominant_project and episode_context.dominant_project
to show where time goes across projects.
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

import duckdb

DB_PATH = os.path.join(
    os.environ.get("LYNCHPIN_REPO_ROOT", "."),
    ".lynchpin/enrich/narrative_spans.duckdb",
)
def _db(): return duckdb.connect(DB_PATH, read_only=True)


@dataclass(frozen=True)
class ProjectProfile:
    project: str
    total_hours: float
    deep_work_hours: float
    productive_hours: float
    top_activities: list[tuple[str, float]]
    top_topics: list[tuple[str, float]]
    span_count: int
    episode_count: int
    first_date: date | None
    last_date: date | None
    narrative: str


def project_breakdown(start: date, end: date) -> list[ProjectProfile]:
    """All projects active in a date range, sorted by hours."""
    db = _db()
    rows = db.execute("""
        SELECT
            COALESCE(episode_context.dominant_project, 'unknown') as proj,
            sum("time"."duration_s") / 3600 as hours,
            sum(CASE WHEN behavior.deep_work_candidate = true THEN "time"."duration_s" ELSE 0 END) / 3600 as dw_h,
            sum(CASE WHEN semantic.is_productive = true THEN "time"."duration_s" ELSE 0 END) / 3600 as prod_h,
            count(*) as spans,
            count(DISTINCT episode_context.episode_id) as episodes,
            min("time"."local_date") as first_d,
            max("time"."local_date") as last_d
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
        GROUP BY proj
        HAVING hours > 0.5
        ORDER BY hours DESC
    """, [start.isoformat(), end.isoformat()]).fetchall()
    db.close()

    profiles = []
    for r in rows:
        profile = _build_project_profile(r)
        profiles.append(profile)

    return profiles


def _build_project_profile(row) -> ProjectProfile:
    proj = row[0] or "unknown"
    hours = row[1] or 0
    dw_h = row[2] or 0
    prod_h = row[3] or 0
    spans = row[4] or 0
    episodes = row[5] or 0

    # Get top activities for this project
    db = _db()
    acts = db.execute("""
        SELECT semantic.activity, sum("time"."duration_s") / 3600 as h
        FROM focus_spans_v2
        WHERE episode_context.dominant_project = ?
        GROUP BY 1 ORDER BY h DESC LIMIT 5
    """, [proj]).fetchall()
    topics = db.execute("""
        SELECT semantic.topic_category, sum("time"."duration_s") / 3600 as h
        FROM focus_spans_v2
        WHERE episode_context.dominant_project = ?
          AND semantic.topic_category IS NOT NULL
        GROUP BY 1 ORDER BY h DESC LIMIT 5
    """, [proj]).fetchall()
    db.close()

    top_acts = [(a[0], a[1]) for a in acts]
    top_topics = [(t[0], t[1]) for t in topics]

    dw_pct = (dw_h / hours * 100) if hours > 0 else 0
    prod_pct = (prod_h / hours * 100) if hours > 0 else 0
    narrative = (f"{proj}: {hours:.1f}h ({spans} spans, {episodes or '?'} episodes). "
                 f"Deep work: {dw_pct:.0f}%, productive: {prod_pct:.0f}%."
                 + (f" Top: {', '.join(a for a,_ in top_acts[:3])}." if top_acts else ""))

    return ProjectProfile(
        project=proj, total_hours=hours, deep_work_hours=dw_h,
        productive_hours=prod_h,
        top_activities=top_acts, top_topics=top_topics,
        span_count=spans, episode_count=episodes or 0,
        first_date=row[6], last_date=row[7],
        narrative=narrative,
    )


def project_timeline(project: str, days: int = 90) -> dict:
    """Daily activity hours for a specific project over time."""
    db = _db()
    daily = db.execute("""
        SELECT "time"."local_date",
               sum("time"."duration_s") / 3600 as hours,
               sum(CASE WHEN behavior.deep_work_candidate = true THEN "time"."duration_s" ELSE 0 END) / 3600 as dw_h,
               count(*) as spans
        FROM focus_spans_v2
        WHERE episode_context.dominant_project = ?
          AND "time"."local_date" >= ?::DATE
        GROUP BY "time"."local_date" ORDER BY "time"."local_date"
    """, [project, (date.today() - timedelta(days=days)).isoformat()]).fetchall()
    db.close()

    dates = [str(r[0]) for r in daily]
    hours = [r[1] for r in daily]
    dw = [r[2] for r in daily]
    spans = [r[3] for r in daily]

    total_h = sum(hours)
    active_days = sum(1 for h in hours if h > 0.1)

    return {
        "project": project,
        "days": len(daily),
        "active_days": active_days,
        "total_hours": total_h,
        "daily_hours": hours,
        "daily_deep_work": dw,
        "dates": dates,
        "trend": "growing" if len(hours) > 14 and sum(hours[-7:]) > sum(hours[:7]) * 1.2
                 else "shrinking" if len(hours) > 14 and sum(hours[-7:]) < sum(hours[:7]) * 0.8
                 else "stable",
    }


def top_projects(d: date | None = None, n: int = 10) -> list[ProjectProfile]:
    """Top projects for the week containing d."""
    if d is None: d = date.today()
    mon = d - timedelta(days=d.weekday())
    sun = mon + timedelta(days=6)
    return project_breakdown(mon, sun)[:n]
