"""Circadian narratives — when do you do what?

Hourly activity profiles, optimal timing analysis, and day-type patterns
from v2 span time.part_of_day and time.circadian_bucket fields.
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
class HourlyProfile:
    activity: str
    hours: list[float]  # 24 values
    peak_hour: int
    peak_value: float
    narrative: str


def hourly_activity_profile(activity: str | None = None,
                            start: date | None = None,
                            end: date | None = None,
                            min_hours: float = 0.5) -> list[HourlyProfile]:
    """Hourly distribution of activities — when does each activity happen?"""
    if end is None: end = date.today()
    if start is None: start = end - timedelta(days=30)

    db = _db()

    if activity:
        rows = db.execute("""
            SELECT EXTRACT(HOUR FROM "time"."local_start"::TIMESTAMP)::INT as h,
                   sum("time"."duration_s") / 3600
            FROM focus_spans_v2
            WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
              AND semantic.activity = ?
            GROUP BY h ORDER BY h
        """, [start.isoformat(), end.isoformat(), activity]).fetchall()
        profiles_data = [(activity, rows)]
    else:
        rows = db.execute("""
            SELECT semantic.activity,
                   EXTRACT(HOUR FROM "time"."local_start"::TIMESTAMP)::INT as h,
                   sum("time"."duration_s") / 3600
            FROM focus_spans_v2
            WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
            GROUP BY semantic.activity, h
            ORDER BY semantic.activity, h
        """, [start.isoformat(), end.isoformat()]).fetchall()

        # Group by activity
        by_act = defaultdict(list)
        for r in rows:
            by_act[r[0]].append((r[1], r[2]))
        profiles_data = by_act.items()

    db.close()

    profiles = []
    for act, hour_data in profiles_data:
        hours = [0.0] * 24
        for h, val in hour_data:
            if 0 <= h < 24:
                hours[h] = val

        total = sum(hours)
        if total < min_hours:
            continue

        peak_h = max(range(24), key=lambda i: hours[i])
        peak_val = hours[peak_h]

        # Find the "shape": morning (6-12), afternoon (12-18), evening (18-24), night (0-6)
        morning = sum(hours[6:12])
        afternoon = sum(hours[12:18])
        evening = sum(hours[18:24])
        night = sum(hours[0:6])

        shape = max(
            [("morning", morning), ("afternoon", afternoon),
             ("evening", evening), ("night", night)],
            key=lambda x: x[1],
        )[0]

        profiles.append(HourlyProfile(
            activity=act, hours=hours, peak_hour=peak_h, peak_value=peak_val,
            narrative=f"{act}: {total:.1f}h, peak at {peak_h:02d}:00 ({peak_val:.1f}h), "
                      f"mostly {shape} ({morning:.0f}/{afternoon:.0f}/{evening:.0f}/{night:.0f})",
        ))

    return sorted(profiles, key=lambda p: -sum(p.hours))


def part_of_day_breakdown(start: date | None = None, end: date | None = None) -> dict:
    """Activity breakdown by part of day (morning/afternoon/evening/night)."""
    if end is None: end = date.today()
    if start is None: start = end - timedelta(days=30)

    db = _db()
    rows = db.execute("""
        SELECT "time"."part_of_day",
               semantic.activity,
               sum("time"."duration_s") / 3600 as h
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
        GROUP BY "time"."part_of_day", semantic.activity
        ORDER BY "time"."part_of_day", h DESC
    """, [start.isoformat(), end.isoformat()]).fetchall()
    db.close()

    by_part = defaultdict(list)
    for r in rows:
        by_part[r[0]].append((r[1], r[2]))

    result = {}
    for part, acts in by_part.items():
        total = sum(h for _, h in acts)
        result[part] = {
            "total_hours": total,
            "top_activities": acts[:5],
        }

    # Build narrative
    parts = []
    for part in ["morning", "afternoon", "evening", "night"]:
        if part in result:
            d = result[part]
            top = ", ".join(f"{a}({h:.1f}h)" for a, h in d["top_activities"][:3])
            parts.append(f"{part}: {d['total_hours']:.1f}h ({top})")
    result["_narrative"] = " | ".join(parts)

    return result
