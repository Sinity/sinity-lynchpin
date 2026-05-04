"""Narrative query surface — chat-friendly access to v2 annotated spans + v3 analytics.

Import: from lynchpin.narrative import query, day_brief, week_story, day_v3

The v2 spans (from GPT 5.5 Pro) provide story-level detail: memory anchors,
episode context, transitions. The v3 analytics (from deep_analysis bundle)
provide statistical layer: active-hour-normalized activity breakdowns, day
archetypes, anomaly scores, circadian profiles, and cross-source correlations.

Combine them:
    from lynchpin.narrative import day_v3
    d = day_v3(date(2026, 1, 15))
    # d.v2_spans → narrative-worthy moments
    # d.v3_metrics → active-hour-normalized statistics
    # d.anomaly → None or anomaly details
    # d.archetype → day type label
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


@dataclass(frozen=True)
class DayV3Result:
    """Combined v2 narrative + v3 analytics for a single day."""
    date: date
    label: str               # "Monday March 15, 2026"
    # v2 — story-level detail
    v2_spans: list[dict]
    high_priority_count: int
    memory_anchors: list[str]
    episode_label: str | None
    # v3 — statistical layer (None if outside v3 window)
    v3_metrics: dict | None
    archetype: str | None
    anomaly: dict | None
    # Derived summary
    summary: str              # 1-2 sentence natural-language summary


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


# ── V3-enriched day ────────────────────────────────────────────────────────

def day_v3(d: date) -> DayV3Result:
    """Single day enriched with both v2 narrative spans and v3 analytics.

    This is the primary entry point for day-level LLM consumption — it combines
    story-worthy moments (v2) with statistical context (v3) in one call.
    """
    # ── v2: narrative spans ──────────────────────────────────────────────
    v2_result = query(start=d, end=d, story_priority="notable", limit=50)
    v2_spans = v2_result.spans

    anchors = []
    for s in v2_spans:
        a = s.get("memory_anchor", "")
        if a and a != "None" and a not in anchors:
            anchors.append(a)

    high_n = sum(1 for s in v2_spans if s.get("story_priority") == "high")

    episode = None
    for s in v2_spans:
        ep = s.get("episode_label", "")
        if ep and ep != "None":
            episode = str(ep)
            break

    # ── v3: daily metrics ────────────────────────────────────────────────
    v3_metrics = None
    archetype = None
    anomaly = None
    try:
        from .v3 import daily as v3_daily, anomaly_for_date
        dm = v3_daily(d)
        if dm is not None:
            v3_metrics = {
                "active_hours": dm.active_hours,
                "productive_h": dm.productive_h,
                "productive_ratio": dm.productive_ratio,
                "deep_candidate_h": dm.deep_candidate_h,
                "deep_ratio": dm.deep_ratio,
                "nsfw_h": dm.nsfw_h,
                "nsfw_ratio": dm.nsfw_ratio,
                "fragmentation": dm.fragmentation,
                "activity_entropy_bits": dm.activity_entropy_bits,
                "switches_per_active_h": dm.switches_per_active_h,
                "top_activities": dm.top_activities,
                "top_topics": dm.top_topics,
                "top_macros": dm.top_macros,
                "sleep_hours": dm.sleep_hours,
                "sleep_score": dm.sleep_score,
                "stress_avg": dm.stress_avg,
                "heart_rate_avg": dm.heart_rate_avg,
                "steps": dm.steps,
                "commit_count": dm.commit_count,
                "shell_commands": dm.shell_commands,
                "ai_session_count": dm.ai_session_count,
            }
            archetype = dm.archetype_label

        anom = anomaly_for_date(d)
        if anom is not None:
            anomaly = {
                "outlier_score": anom.outlier_score,
                "reasons": anom.reasons[:5],
            }
    except Exception:
        pass

    # ── Build summary ────────────────────────────────────────────────────
    parts = [d.strftime("%A %B %d, %Y")]
    if v3_metrics:
        ah = v3_metrics["active_hours"]
        pr = v3_metrics["productive_ratio"]
        parts.append(f"{ah:.1f}h active, {pr:.0%} productive")
        if archetype:
            parts.append(f"({archetype})")
    parts.append(f"{len(v2_spans)} narrative spans, {high_n} high-priority")
    if anchors:
        parts.append(f"Key: {'; '.join(anchors[:3])}")

    return DayV3Result(
        date=d,
        label=d.strftime("%A %B %d, %Y"),
        v2_spans=v2_spans,
        high_priority_count=high_n,
        memory_anchors=anchors,
        episode_label=episode,
        v3_metrics=v3_metrics,
        archetype=archetype,
        anomaly=anomaly,
        summary=". ".join(parts),
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
