"""Behavioral trend narratives — how scores change over time.

Tracks productive_score, focus_score, learning_score, maker_score,
fragmentation_score, and deep_work ratio from v2 spans.
"""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

import duckdb

DB_PATH = os.path.join(
    os.environ.get("LYNCHPIN_REPO_ROOT", "."),
    ".lynchpin/enrich/narrative_spans.duckdb",
)
def _db(): return duckdb.connect(DB_PATH, read_only=True)

SCORES = ["productive_score", "focus_score", "learning_score",
          "maker_score", "fragmentation_score"]


@dataclass(frozen=True)
class ScoreTrend:
    name: str
    current: float
    avg_7d: float
    avg_30d: float
    trend_7d: str      # "up", "down", "stable"
    trend_30d: str
    min_30d: float
    max_30d: float
    narrative: str      # one-line interpretation


@dataclass(frozen=True)
class BehavioralSnapshot:
    date: date
    days: int
    scores: dict[str, ScoreTrend]
    deep_work_ratio_30d: float
    deep_work_trend: str
    novelty_ratio: float  # fraction of spans marked as novel
    summary: str


def behavioral_snapshot(d: date | None = None, days: int = 30) -> BehavioralSnapshot:
    """Current behavioral scores with 7d/30d trends."""
    if d is None: d = date.today()
    end = d
    start = d - timedelta(days=days)

    db = _db()
    daily = db.execute("""
        SELECT "time"."local_date",
               avg(behavior.productive_score),
               avg(behavior.focus_score),
               avg(behavior.learning_score),
               avg(behavior.maker_score),
               avg(behavior.fragmentation_score),
               avg(CASE WHEN behavior.deep_work_candidate = true THEN 1.0 ELSE 0.0 END),
               avg(CASE WHEN behavior.novelty = 'novel' THEN 1.0 ELSE 0.0 END),
               count(*)
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
        GROUP BY "time"."local_date" ORDER BY "time"."local_date"
    """, [start.isoformat(), end.isoformat()]).fetchall()
    db.close()

    if not daily:
        return BehavioralSnapshot(date=d, days=0, scores={},
                                  deep_work_ratio_30d=0, deep_work_trend="no data",
                                  novelty_ratio=0, summary="No behavioral data.")

    # Build daily arrays per score
    daily_scores = {s: [] for s in SCORES}
    daily_dw = []
    daily_novelty = []
    for r in daily:
        for i, s in enumerate(SCORES):
            daily_scores[s].append(r[i+1] or 0)
        daily_dw.append(r[6] or 0)
        daily_novelty.append(r[7] or 0)

    def trend(vals, window):
        if len(vals) < window: return 0
        recent = vals[-window:]
        prior = vals[-window*2:-window] if len(vals) >= window*2 else vals[:window]
        avg_recent = sum(recent) / len(recent)
        avg_prior = sum(prior) / len(prior) if prior else avg_recent
        if avg_prior == 0: return 0
        return (avg_recent - avg_prior) / avg_prior

    def trend_label(change):
        if change > 0.05: return "up"
        if change < -0.05: return "down"
        return "stable"

    scores = {}
    for s in SCORES:
        vals = daily_scores[s]
        if not vals: continue
        cur = vals[-1]
        avg7 = sum(vals[-7:]) / min(7, len(vals)) if vals else 0
        avg30 = sum(vals) / len(vals) if vals else 0
        t7 = trend_label(trend(vals, 7))
        t30 = trend_label(trend(vals, 30))
        mn = min(vals) if vals else 0
        mx = max(vals) if vals else 0

        narr = f"{s}: {cur:.2f} (7d avg {avg7:.2f}, "
        if t7 == "up": narr += "rising ↗)"
        elif t7 == "down": narr += "falling ↘)"
        else: narr += "stable →)"

        scores[s] = ScoreTrend(
            name=s, current=cur, avg_7d=avg7, avg_30d=avg30,
            trend_7d=t7, trend_30d=t30, min_30d=mn, max_30d=mx,
            narrative=narr,
        )

    dw_ratio = sum(daily_dw) / len(daily_dw) if daily_dw else 0
    dw_trend = trend_label(trend(daily_dw, 7))
    novelty = sum(daily_novelty) / len(daily_novelty) if daily_novelty else 0

    # Build summary
    up = [s for s in scores.values() if s.trend_7d == "up"]
    down = [s for s in scores.values() if s.trend_7d == "down"]
    parts = [f"Behavioral snapshot ({start} → {end}):"]
    if up: parts.append(f"Improving: {', '.join(s.name for s in up)}.")
    if down: parts.append(f"Declining: {', '.join(s.name for s in down)}.")
    parts.append(f"Deep work: {dw_ratio:.0%} ({dw_trend}).")
    parts.append(f"Novelty: {novelty:.0%} of spans are novel activities.")

    return BehavioralSnapshot(
        date=d, days=len(daily), scores=scores,
        deep_work_ratio_30d=dw_ratio, deep_work_trend=dw_trend,
        novelty_ratio=novelty, summary=" ".join(parts),
    )


def score_comparison(period_a: tuple[date, date],
                     period_b: tuple[date, date]) -> dict:
    """Compare behavioral scores between two periods."""
    db = _db()

    def get_avgs(start, end):
        return db.execute("""
            SELECT avg(behavior.productive_score),
                   avg(behavior.focus_score),
                   avg(behavior.learning_score),
                   avg(behavior.maker_score),
                   avg(behavior.fragmentation_score),
                   avg(CASE WHEN behavior.deep_work_candidate = true THEN 1.0 ELSE 0.0 END),
                   count(*)
            FROM focus_spans_v2
            WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
        """, [start.isoformat(), end.isoformat()]).fetchone()

    a = get_avgs(*period_a)
    b = get_avgs(*period_b)
    db.close()

    result = {}
    for i, s in enumerate(SCORES):
        if a and b and a[i] and b[i]:
            delta = b[i] - a[i]
            pct = delta / abs(a[i]) * 100 if a[i] else 0
            result[s] = {"before": a[i], "after": b[i], "delta": delta,
                         "pct_change": pct,
                         "direction": "up" if delta > 0.01 else "down" if delta < -0.01 else "stable"}

    # Deep work
    if a and b:
        result["deep_work_ratio"] = {"before": a[5], "after": b[5],
                                     "delta": b[5] - a[5],
                                     "direction": "up" if b[5] > a[5] else "down"}
    return result
