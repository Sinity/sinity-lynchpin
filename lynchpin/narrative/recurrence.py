"""Recurrence & habit narratives — what keeps coming back?

Uses v2 title_global stats to find: persistent habits, abandoned projects,
new interests, and cyclical patterns in focus.
"""
from __future__ import annotations

import os
from collections import Counter
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
class RecurringTitle:
    rank: int
    global_rows: int
    total_hours: float
    first_date: date
    last_date: date
    days_active: int
    dominant_app: str
    dominant_project: str | None
    activity: str
    narrative: str


def top_habits(n: int = 20) -> list[RecurringTitle]:
    """Most persistent recurring focus targets — highest row counts."""
    db = _db()
    rows = db.execute(f"""
        SELECT DISTINCT
            context_window.title_global.rank_by_rows,
            context_window.title_global.global_rows,
            context_window.title_global.global_seconds / 3600,
            context_window.title_global.first_date,
            context_window.title_global.last_date,
            context_window.title_global.dominant_app,
            context_window.title_global.dominant_project,
            semantic.activity
        FROM focus_spans_v2
        WHERE context_window.title_global.global_rows > 10
        ORDER BY context_window.title_global.global_rows DESC
        LIMIT {n}
    """).fetchall()
    db.close()

    habits = []
    for r in rows:
        days = (r[4] - r[3]).days if r[3] and r[4] else 0
        habits.append(RecurringTitle(
            rank=r[0] or 0, global_rows=r[1] or 0, total_hours=r[2] or 0,
            first_date=r[3], last_date=r[4], days_active=days,
            dominant_app=r[5] or "?", dominant_project=r[6],
            activity=r[7] or "unknown",
            narrative=(f"#{r[0]}: {r[1]}x over {days}d "
                       f"({r[2]:.0f}h, {r[5] or '?'}/{r[7] or '?'})"
                       + (f" [{r[6]}]" if r[6] else "")),
        ))
    return habits


def recent_emergence(d: date | None = None, days: int = 14) -> list[RecurringTitle]:
    """Titles that first appeared recently — new interests."""
    if d is None: d = date.today()
    cutoff = d - timedelta(days=days)

    db = _db()
    rows = db.execute(f"""
        SELECT DISTINCT
            context_window.title_global.rank_by_rows,
            context_window.title_global.global_rows,
            context_window.title_global.global_seconds / 3600,
            context_window.title_global.first_date,
            context_window.title_global.last_date,
            context_window.title_global.dominant_app,
            context_window.title_global.dominant_project,
            semantic.activity
        FROM focus_spans_v2
        WHERE context_window.title_global.first_date >= ?::DATE
          AND context_window.title_global.global_rows > 3
        ORDER BY context_window.title_global.global_rows DESC
        LIMIT 20
    """, [cutoff.isoformat()]).fetchall()
    db.close()

    results = []
    for r in rows:
        results.append(RecurringTitle(
            rank=r[0] or 0, global_rows=r[1] or 0, total_hours=r[2] or 0,
            first_date=r[3], last_date=r[4],
            days_active=(r[4] - r[3]).days if r[3] and r[4] else 0,
            dominant_app=r[5] or "?", dominant_project=r[6],
            activity=r[7] or "unknown",
            narrative=f"NEW: {r[1]}x, {r[2]:.0f}h [{r[5] or '?'}/{r[7] or '?'}]"
                       + (f" [{r[6]}]" if r[6] else ""),
        ))
    return results


def recently_gone(d: date | None = None, days: int = 30) -> list[RecurringTitle]:
    """Titles that haven't been seen recently — abandoned or completed."""
    if d is None: d = date.today()
    cutoff = d - timedelta(days=days)

    db = _db()
    rows = db.execute(f"""
        SELECT DISTINCT
            context_window.title_global.rank_by_rows,
            context_window.title_global.global_rows,
            context_window.title_global.global_seconds / 3600,
            context_window.title_global.first_date,
            context_window.title_global.last_date,
            context_window.title_global.dominant_app,
            context_window.title_global.dominant_project,
            semantic.activity
        FROM focus_spans_v2
        WHERE context_window.title_global.last_date < ?::DATE
          AND context_window.title_global.last_date > ?::DATE
          AND context_window.title_global.global_rows > 20
        ORDER BY context_window.title_global.global_rows DESC
        LIMIT 20
    """, [cutoff.isoformat(), (d - timedelta(days=365)).isoformat()]).fetchall()
    db.close()

    results = []
    for r in rows:
        days_since = (d - r[4]).days if r[4] else 0
        results.append(RecurringTitle(
            rank=r[0] or 0, global_rows=r[1] or 0, total_hours=r[2] or 0,
            first_date=r[3], last_date=r[4],
            days_active=(r[4] - r[3]).days if r[3] and r[4] else 0,
            dominant_app=r[5] or "?", dominant_project=r[6],
            activity=r[7] or "unknown",
            narrative=f"GONE {days_since}d: {r[1]}x, {r[2]:.0f}h [{r[5] or '?'}/{r[7] or '?'}]"
                       + (f" [{r[6]}]" if r[6] else ""),
        ))
    return results
