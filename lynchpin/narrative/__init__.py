"""Narrative query surface — chat-friendly access to v2 annotated spans.

Import: from lynchpin.narrative import query, day_brief, week_story

The v2 spans (from GPT 5.5 Pro) use DuckDB STRUCT types with dot notation:
  semantic.activity, semantic.attention_level, semantic.context_sentence
  time.local_date, time.duration_s, time.start_s, time.part_of_day
  memory.story_priority, memory.memory_anchor, memory.why_it_mattered
  behavior.productive_score, behavior.deep_work_candidate
  context_window.transition_from_previous, context_window.transition_to_next
  episode_context.episode_label, episode_context.episode_thesis
  literal_parse.domains, literal_parse.command, literal_parse.file_paths
"""
from __future__ import annotations

import json, os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Sequence

import duckdb

DB_PATH = os.path.join(
    os.environ.get("LYNCHPIN_REPO_ROOT", "."),
    ".lynchpin/enrich/narrative_spans.duckdb",
)


def _db():
    return duckdb.connect(DB_PATH, read_only=True)


@dataclass(frozen=True)
class NarrativeResult:
    title: str
    summary: str
    evidence: list[dict]
    spans: list[dict]
    stats: dict


# ── Query ─────────────────────────────────────────────────────────────────

def query(
    *,
    start: date,
    end: date,
    story_priority: str = "notable",  # "high", "notable" (high+medium), "all"
    activity: str | None = None,
    limit: int = 100,
) -> NarrativeResult:
    """Query narrative-worthy spans for a date range."""
    db = _db()
    conds = ['"time"."local_date" >= ?::DATE', '"time"."local_date" <= ?::DATE']
    params = [start.isoformat(), end.isoformat()]

    if story_priority == "high":
        conds.append('"memory"."story_priority" = \'high\'')
    elif story_priority == "notable":
        conds.append('"memory"."story_priority" IN (\'high\', \'medium\')')
    elif story_priority != "all":
        conds.append('"memory"."story_priority" = ?')
        params.append(story_priority)

    if activity:
        conds.append('"semantic"."activity" = ?')
        params.append(activity)

    where = " AND ".join(conds)
    rows = db.execute(
        f"""SELECT * FROM focus_spans_v2
        WHERE {where}
        ORDER BY "time"."local_date" DESC, "time"."start_s" ASC
        LIMIT ?""",
        params + [limit],
    ).fetchall()

    cols = [c[0] for c in db.execute("DESCRIBE focus_spans_v2").fetchall()]
    db.close()

    spans = [_flatten_row(dict(zip(cols, r))) for r in rows]

    evidence = []
    for s in spans:
        anchor = s.get("memory_anchor", "")
        ctx = s.get("context_sentence", "")
        ld = s.get("local_date", "")
        d = str(ld)[:10] if ld else ""
        act = s.get("activity", "")
        if anchor and anchor != "None":
            evidence.append({"date": d, "claim": anchor, "activity": act,
                           "why": s.get("why_it_mattered", "")})
        elif ctx and ctx != "None":
            evidence.append({"date": d, "claim": f"[{act}] {ctx}", "activity": act})

    total_h = sum(float(s.get("duration_s", 0) or 0) / 3600 for s in spans)
    high_n = sum(1 for s in spans if s.get("story_priority") == "high")

    return NarrativeResult(
        title=f"{start} → {end}",
        summary=f"{len(spans)} spans, {total_h:.1f}h, {high_n} high-priority stories",
        evidence=evidence[:30],
        spans=spans,
        stats={"n": len(spans), "hours": total_h, "high_priority": high_n,
               "range": f"{start} → {end}"},
    )


# ── Day brief ─────────────────────────────────────────────────────────────

def day_brief(d: date) -> NarrativeResult:
    """One day — story-worthy moments + activity breakdown."""
    result = query(start=d, end=d, story_priority="notable", limit=50)
    spans = result.spans
    if not spans:
        return NarrativeResult(title=str(d), summary=f"No data for {d}.",
                               evidence=[], spans=[], stats={})

    total_h = result.stats["hours"]
    acts = {}
    for s in spans:
        a = s.get("activity", "unknown")
        h = float(s.get("duration_s", 0) or 0) / 3600
        acts[a] = acts.get(a, 0) + h
    top_acts = sorted(acts.items(), key=lambda x: -x[1])[:5]
    dw = sum(float(s.get("duration_s", 0) or 0) / 3600
             for s in spans if s.get("deep_work_candidate") == "True")

    return NarrativeResult(
        title=d.strftime("%A %B %d, %Y"),
        summary=f"{total_h:.1f}h: {', '.join(f'{a}({h:.1f}h)' for a,h in top_acts)}. "
                f"{dw:.1f}h deep work candidate. "
                f"{sum(1 for s in spans if s.get('story_priority')=='high')} notable moments.",
        evidence=result.evidence[:10],
        spans=spans,
        stats={"hours": total_h, "deep_work_hours": dw,
               "activities": dict(top_acts)},
    )


# ── Week story ────────────────────────────────────────────────────────────

def week_story(d: date | None = None) -> NarrativeResult:
    """Full narrative week — daily breakdowns + transitions + high-priority stories."""
    if d is None:
        d = date.today()
    mon = d - timedelta(days=d.weekday())
    sun = mon + timedelta(days=6)

    result = query(start=mon, end=sun, story_priority="notable", limit=300)
    spans = result.spans

    # Daily breakdown
    daily = {}
    for s in spans:
        ld = s.get("local_date") or ""
        day = str(ld)[:10]
        if day not in daily:
            daily[day] = {"hours": 0, "acts": {}, "stories": 0}
        daily[day]["hours"] += float(s.get("duration_s", 0) or 0) / 3600
        a = s.get("activity", "unknown")
        daily[day]["acts"][a] = daily[day]["acts"].get(a, 0) + 1
        if s.get("story_priority") == "high":
            daily[day]["stories"] += 1

    # Transition summary
    transitions = {}
    for s in spans:
        t = s.get("transition_from_previous", "")
        if t and t != "None":
            try:
                t_type = t.get("type", "unknown") if isinstance(t, dict) else str(t)
            except Exception:
                t_type = str(t)[:30]
            transitions[t_type] = transitions.get(t_type, 0) + 1

    total_h = sum(d["hours"] for d in daily.values())
    high_n = sum(1 for s in spans if s.get("story_priority") == "high")

    return NarrativeResult(
        title=f"Week of {mon.strftime('%B %d, %Y')}",
        summary=f"{len(spans)} spans, {total_h:.1f}h, {high_n} high-priority stories. "
                f"{len(daily)} days with data.",
        evidence=result.evidence[:30],
        spans=spans,
        stats={
            "hours": total_h, "high_priority": high_n,
            "daily": {day: {"hours": d["hours"], "stories": d["stories"],
                           "top_acts": dict(sorted(d["acts"].items(), key=lambda x: -x[1])[:5])}
                     for day, d in sorted(daily.items())},
            "top_transitions": dict(sorted(transitions.items(), key=lambda x: -x[1])[:10]),
        },
    )


# ── Helpers ───────────────────────────────────────────────────────────────

def _flatten_row(d: dict) -> dict:
    """Flatten DuckDB STRUCT columns into a flat dict for easier access."""
    out = {}
    for key, val in d.items():
        if isinstance(val, dict):
            for subkey, subval in val.items():
                out[subkey] = subval
        else:
            out[key] = val
    return out
