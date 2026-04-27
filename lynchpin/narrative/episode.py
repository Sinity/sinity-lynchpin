"""Episode narrative — work session analysis from v2 annotated spans.

Episodes group consecutive focus spans into coherent work sessions.
Each episode has a thesis statement, dominant activity/topic, and spans
with start/middle/end roles. This is the natural unit between individual
spans and full days — ideal for "what was I doing" questions.
"""
from __future__ import annotations

import json, os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Sequence

import duckdb

DB_PATH = os.path.join(
    os.environ.get("LYNCHPIN_REPO_ROOT", "."),
    ".lynchpin/enrich/narrative_spans.duckdb",
)

def _db(): return duckdb.connect(DB_PATH, read_only=True)


@dataclass(frozen=True)
class Episode:
    episode_id: str
    date: date
    start_time: str
    end_time: str
    duration_min: float
    label: str
    thesis: str
    dominant_activity: str
    dominant_topic: str | None
    span_count: int
    deep_work: bool
    productive_score: float
    focus_score: float
    span_activities: list[str]
    story_priority: str

    @property
    def one_liner(self) -> str:
        dw = "⚡" if self.deep_work else "  "
        return (f"{dw} {self.duration_min:.0f}min {self.dominant_activity}"
                + (f" / {self.dominant_topic}" if self.dominant_topic else "")
                + f": {self.thesis[:100]}")


@dataclass(frozen=True)
class DayEpisodes:
    date: date
    episodes: list[Episode]
    total_focus_min: float
    deep_work_min: float
    episode_count: int
    story: str  # narrative flow through the day


# ── Query ─────────────────────────────────────────────────────────────────

def day_episodes(d: date) -> DayEpisodes:
    """All episodes for one day, ordered by time."""
    db = _db()
    rows = db.execute("""
        SELECT
            episode_context.episode_id,
            "time"."local_date",
            min("time"."start_s") as first_start,
            max("time"."start_s" + "time"."duration_s") as last_end,
            max(episode_context.episode_duration_s) as dur_s,
            max(episode_context.episode_label) as label,
            max(episode_context.episode_thesis) as thesis,
            max(episode_context.dominant_activity) as dom_act,
            max(episode_context.dominant_topic) as dom_topic,
            count(*) as span_n,
            max(CASE WHEN behavior.deep_work_candidate = true THEN 1 ELSE 0 END) as dw,
            avg(behavior.productive_score) as prod_score,
            avg(behavior.focus_score) as focus_score,
            list(semantic.activity) as activities,
            max(CASE WHEN "memory"."story_priority" = 'high' THEN 'high'
                     WHEN "memory"."story_priority" = 'medium' THEN 'medium'
                     ELSE 'low' END) as story
        FROM focus_spans_v2
        WHERE "time"."local_date" = ?::DATE
          AND episode_context.episode_id IS NOT NULL
        GROUP BY episode_context.episode_id, "time"."local_date"
        ORDER BY first_start ASC
    """, [d.isoformat()]).fetchall()
    db.close()

    episodes = []
    for r in rows:
        first_s = r[2] or 0
        last_s = r[3] or first_s
        dur_s = r[4] or (last_s - first_s)
        activities = r[11] or []
        episodes.append(Episode(
            episode_id=r[0], date=d,
            start_time=_format_time_of_day(first_s),
            end_time=_format_time_of_day(last_s),
            duration_min=dur_s / 60,
            label=r[5] or "", thesis=r[6] or "",
            dominant_activity=r[7] or "unknown",
            dominant_topic=r[8],
            span_count=r[9] or 0,
            deep_work=bool(r[10]),
            productive_score=r[12] or 0,
            focus_score=r[13] or 0,
            span_activities=activities,
            story_priority=r[14] or "low",
        ))

    total_min = sum(ep.duration_min for ep in episodes)
    dw_min = sum(ep.duration_min for ep in episodes if ep.deep_work)

    # Build narrative flow
    if episodes:
        flow_parts = [f"{d.strftime('%A %B %d')}: {len(episodes)} episodes, {total_min:.0f}min."]
        for ep in episodes[:8]:
            flow_parts.append(ep.one_liner)
        story = "\n".join(flow_parts)
    else:
        story = f"{d.strftime('%A %B %d')}: no episode data."

    return DayEpisodes(
        date=d, episodes=episodes,
        total_focus_min=total_min, deep_work_min=dw_min,
        episode_count=len(episodes), story=story,
    )


def week_episodes(d: date | None = None) -> list[DayEpisodes]:
    """Episodes for every day in a week."""
    if d is None: d = date.today()
    mon = d - timedelta(days=d.weekday())
    days = []
    for i in range(7):
        day = mon + timedelta(days=i)
        de = day_episodes(day)
        if de.episodes:
            days.append(de)
    return days


def top_episodes(start: date, end: date, n: int = 20,
                 deep_work_only: bool = False,
                 min_minutes: float = 15,
                 ) -> list[Episode]:
    """Top episodes in a date range by duration."""
    db = _db()
    conds = [
        '"time"."local_date" >= ?::DATE',
        '"time"."local_date" <= ?::DATE',
        'episode_context.episode_id IS NOT NULL',
    ]
    params = [start.isoformat(), end.isoformat()]
    if deep_work_only:
        conds.append('behavior.deep_work_candidate = true')

    where = " AND ".join(conds)
    rows = db.execute(f"""
        SELECT
            episode_context.episode_id,
            "time"."local_date",
            min("time"."start_s") as first_start,
            max("time"."start_s" + "time"."duration_s") as last_end,
            max(episode_context.episode_duration_s) as dur_s,
            max(episode_context.episode_label) as label,
            max(episode_context.episode_thesis) as thesis,
            max(episode_context.dominant_activity) as dom_act,
            max(episode_context.dominant_topic) as dom_topic,
            count(*) as span_n,
            max(CASE WHEN behavior.deep_work_candidate = true THEN 1 ELSE 0 END) as dw,
            avg(behavior.productive_score) as prod_score,
            avg(behavior.focus_score) as focus_score,
            list(semantic.activity) as activities,
            max(CASE WHEN "memory"."story_priority" = 'high' THEN 'high'
                     WHEN "memory"."story_priority" = 'medium' THEN 'medium'
                     ELSE 'low' END) as story
        FROM focus_spans_v2
        WHERE {where}
        GROUP BY episode_context.episode_id, "time"."local_date"
        HAVING max(episode_context.episode_duration_s) >= {min_minutes * 60}
        ORDER BY max(episode_context.episode_duration_s) DESC
        LIMIT {n}
    """, params).fetchall()
    db.close()

    episodes = []
    for r in rows:
        first_s = r[2] or 0
        last_s = r[3] or first_s
        d = r[1]
        episodes.append(Episode(
            episode_id=r[0], date=d,
            start_time=_format_time_of_day(first_s),
            end_time=_format_time_of_day(last_s),
            duration_min=(r[4] or (last_s - first_s)) / 60,
            label=r[5] or "", thesis=r[6] or "",
            dominant_activity=r[7] or "unknown", dominant_topic=r[8],
            span_count=r[9] or 0, deep_work=bool(r[10]),
            productive_score=r[11] or 0, focus_score=r[12] or 0,
            span_activities=r[13] or [],
            story_priority=r[14] or "low",
        ))
    return episodes


def episode_timeline(d: date) -> str:
    """One-line-per-episode timeline for a day — compact chat context."""
    de = day_episodes(d)
    if not de.episodes:
        return f"{d}: no episodes"
    lines = [f"{d.strftime('%A %B %d')} — {len(de.episodes)} episodes, {de.total_focus_min:.0f}min"]
    for ep in de.episodes:
        lines.append(ep.one_liner)
    return "\n".join(lines)


def _format_time_of_day(seconds_since_midnight: float) -> str:
    """Convert epoch seconds to HH:MM."""
    h = int(seconds_since_midnight // 3600) % 24
    m = int((seconds_since_midnight % 3600) // 60)
    return f"{h:02d}:{m:02d}"
