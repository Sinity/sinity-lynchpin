"""Attention flow narrative — how focus moves through the day.

Uses v2 span transition types (editor_to_ai, work_to_media, project_switch, etc.)
to tell the story of attention dynamics. Each day becomes a flow graph.
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Sequence

import duckdb

DB_PATH = os.path.join(
    os.environ.get("LYNCHPIN_REPO_ROOT", "."),
    ".lynchpin/enrich/narrative_spans.duckdb",
)

def _db(): return duckdb.connect(DB_PATH, read_only=True)


@dataclass(frozen=True)
class FlowSpan:
    time: str
    activity: str
    attention: str
    transition_from: str
    transition_to: str
    context: str
    duration_min: float
    deep_work: bool


@dataclass(frozen=True)
class DayFlow:
    date: date
    spans: list[FlowSpan]
    transition_counts: dict[str, int]
    dominant_flow: str        # e.g. "editor ↔ AI loop"
    interruptions: int        # count of work→media/social switches
    flow_narrative: str       # natural language flow story


def day_flow(d: date) -> DayFlow:
    """Attention flow for one day — all spans with transitions."""
    db = _db()
    rows = db.execute("""
        SELECT
            "time"."start_s",
            semantic.activity,
            semantic.attention_level,
            context_window.transition_from_previous.type,
            context_window.transition_to_next.type,
            semantic.context_sentence,
            "time"."duration_s",
            behavior.deep_work_candidate
        FROM focus_spans_v2
        WHERE "time"."local_date" = ?::DATE
        ORDER BY "time"."start_s" ASC
    """, [d.isoformat()]).fetchall()
    db.close()

    if not rows:
        return DayFlow(date=d, spans=[], transition_counts={},
                       dominant_flow="no data", interruptions=0,
                       flow_narrative=f"{d}: no data")

    spans = []
    trans = Counter()
    for r in rows:
        start_s = r[0] or 0
        h = int(start_s // 3600) % 24
        m = int((start_s % 3600) // 60)
        spans.append(FlowSpan(
            time=f"{h:02d}:{m:02d}",
            activity=r[1] or "unknown",
            attention=r[2] or "shallow",
            transition_from=r[3] or "unknown",
            transition_to=r[4] or "unknown",
            context=r[5] or "",
            duration_min=(r[6] or 0) / 60,
            deep_work=bool(r[7]),
        ))
        trans[r[3] or "unknown"] += 1

    # Classify dominant flow pattern
    ai_editor = trans.get("editor_to_ai", 0) + trans.get("ai_to_editor", 0)
    work_media = trans.get("work_to_media", 0) + trans.get("media_to_work", 0)
    project_switches = trans.get("project_switch", 0)
    interruptions = trans.get("work_to_media", 0) + trans.get("work_to_social", 0)

    if ai_editor > 5:
        dominant = f"editor ↔ AI loop ({ai_editor} switches)"
    elif project_switches > 10:
        dominant = f"multi-project ({project_switches} project switches)"
    elif work_media > 5:
        dominant = f"work/media alternation ({work_media} switches)"
    else:
        top = trans.most_common(1)[0] if trans else ("unknown", 0)
        dominant = f"mostly {top[0]} ({top[1]} transitions)"

    # Build narrative
    active_spans = [s for s in spans if s.activity not in ("idle",)]
    narrative_parts = [
        f"{d.strftime('%A %B %d')}: {len(spans)} spans, "
        f"{sum(s.duration_min for s in spans):.0f}min focused.",
        f"Flow: {dominant}. {interruptions} work→distraction switches.",
    ]

    # Find the story arc: what were the 3 main activity blocks?
    blocks = []
    current_act = None
    current_start = None
    current_dur = 0
    for s in spans:
        if s.activity != current_act:
            if current_act and current_dur > 5:
                blocks.append((current_start, current_act, current_dur))
            current_act = s.activity
            current_start = s.time
            current_dur = 0
        current_dur += s.duration_min
    if current_act and current_dur > 5:
        blocks.append((current_start, current_act, current_dur))

    if blocks:
        narrative_parts.append("Arc: " + " → ".join(
            f"{act}({dur:.0f}m)" for _, act, dur in blocks[:8]))

    return DayFlow(
        date=d, spans=spans,
        transition_counts=dict(trans.most_common(15)),
        dominant_flow=dominant,
        interruptions=interruptions,
        flow_narrative=". ".join(narrative_parts),
    )


def week_flow(d: date | None = None) -> list[DayFlow]:
    """Flow for every day in a week."""
    if d is None: d = date.today()
    mon = d - timedelta(days=d.weekday())
    return [day_flow(mon + timedelta(days=i)) for i in range(7)
            if day_flow(mon + timedelta(days=i)).spans]


def interruption_pattern(d: date, days: int = 30) -> dict:
    """Analyze interruption patterns over time."""
    db = _db()
    rows = db.execute("""
        SELECT "time"."local_date",
               count(*) FILTER (WHERE context_window.transition_from_previous.type IN ('work_to_media', 'work_to_social')) as interruptions,
               count(*) as total_spans
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
        GROUP BY "time"."local_date"
        ORDER BY "time"."local_date"
    """, [(d - timedelta(days=days)).isoformat(), d.isoformat()]).fetchall()
    db.close()

    dates = []
    rates = []
    for r in rows:
        dates.append(str(r[0]))
        rates.append(r[1] / max(r[2], 1) * 100 if r[2] else 0)

    return {
        "period": f"{dates[0]} → {dates[-1]}" if dates else "no data",
        "dates": dates,
        "interruption_pct": rates,
        "avg_pct": sum(rates) / len(rates) if rates else 0,
        "trend": "increasing" if len(rates) > 7 and rates[-1] > rates[0] * 1.2
                 else "decreasing" if len(rates) > 7 and rates[-1] < rates[0] * 0.8
                 else "stable",
    }
