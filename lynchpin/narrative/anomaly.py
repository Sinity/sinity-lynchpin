"""Anomaly narratives — what's unusual right now?

Detects daily anomalies in: active hours, deep work ratio, activity mix,
sensitive-content activity, AI tool usage, content novelty, interruption rate.
Uses IQR-based detection with temporal context windows.
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
class Anomaly:
    metric: str
    date: date
    value: float
    expected: float   # median of baseline
    z_score: float    # modified z-score (MAD-based)
    direction: str    # "high" or "low"
    narrative: str


@dataclass(frozen=True)
class DayAnomalies:
    date: date
    anomalies: list[Anomaly]
    unusual: bool
    summary: str


def day_anomalies(d: date, baseline_days: int = 30) -> DayAnomalies:
    """Detect what's unusual about a specific day compared to recent baseline."""
    baseline_start = d - timedelta(days=baseline_days)
    baseline_end = d - timedelta(days=1)

    db = _db()

    # Get daily metrics for baseline
    daily = db.execute("""
        SELECT "time"."local_date",
               sum("time"."duration_s") / 3600 as hours,
               avg(CASE WHEN behavior.deep_work_candidate = true THEN 1.0 ELSE 0.0 END) as dw_ratio,
               avg(CASE WHEN semantic.is_productive = true THEN 1.0 ELSE 0.0 END) as prod_ratio,
               sum(CASE WHEN semantic.topic_category = 'nsfw' THEN "time"."duration_s" ELSE 0 END) / 3600 as nsfw_h,
               sum(CASE WHEN semantic.activity = 'chatting_work' THEN "time"."duration_s" ELSE 0 END) / 3600 as ai_h,
               count(*) as spans,
               avg(CASE WHEN semantic.attention_level = 'deep' THEN 1.0 ELSE 0.0 END) as deep_attn,
               count(DISTINCT semantic.activity) as activity_diversity
        FROM focus_spans_v2
        WHERE "time"."local_date" BETWEEN ?::DATE AND ?::DATE
        GROUP BY "time"."local_date" ORDER BY "time"."local_date"
    """, [baseline_start.isoformat(), d.isoformat()]).fetchall()
    db.close()

    if len(daily) < 7:
        return DayAnomalies(date=d, anomalies=[], unusual=False, summary="Not enough baseline data.")

    # Separate baseline (all except last day) and target (last day)
    baseline = daily[:-1]
    target = daily[-1] if daily[-1][0] == d else None
    if target is None:
        return DayAnomalies(date=d, anomalies=[], unusual=False, summary="No data for target date.")

    metrics = {
        "active_hours": (0, "hours of activity", "high", "🔋 Unusually active day"),
        "deep_work_ratio": (1, "deep work ratio", "high", "⚡ Unusually focused day"),
        "productive_ratio": (2, "productive ratio", "high", "📈 Unusually productive day"),
        "nsfw_hours": (3, "sensitive-content hours", "high", "Unusually high sensitive-content activity"),
        "ai_hours": (4, "AI tool hours", "high", "🤖 Unusually heavy AI usage"),
        "span_count": (5, "span count", "high", "🔄 Unusually fragmented day"),
        "deep_attention": (6, "deep attention ratio", "high", "🧠 Unusually deep attention"),
        "activity_diversity": (7, "activity diversity", "low", "🎯 Unusually focused (low variety)"),
    }

    anomalies = []
    for name, (idx, label, direction, narrative_template) in metrics.items():
        # Get baseline values
        vals = sorted(r[idx] for r in baseline if r[idx] is not None)
        if len(vals) < 7: continue

        target_val = target[idx]
        if target_val is None: continue

        # MAD-based modified z-score
        median = vals[len(vals) // 2]
        mad = sorted(abs(v - median) for v in vals)[len(vals) // 2]
        if mad == 0: continue

        z = 0.6745 * (target_val - median) / mad  # 0.6745 = consistency constant
        if abs(z) < 2.5: continue  # not anomalous

        unusual_direction = ("high" if target_val > median else "low")
        if unusual_direction != direction: continue

        anomalies.append(Anomaly(
            metric=name, date=d, value=target_val, expected=median,
            z_score=z, direction=unusual_direction,
            narrative=f"{narrative_template}: {label} {target_val:.1f} vs usual {median:.1f} (z={z:.1f})",
        ))

    unusual = len(anomalies) > 0
    if unusual:
        summary = f"{d}: {len(anomalies)} anomalies. " + " ".join(a.narrative for a in anomalies[:4])
    else:
        summary = f"{d}: No significant anomalies — typical day."

    return DayAnomalies(date=d, anomalies=anomalies, unusual=unusual, summary=summary)


def week_anomalies(d: date | None = None) -> dict[str, DayAnomalies]:
    """Anomaly check for every day in a week."""
    if d is None: d = date.today()
    mon = d - timedelta(days=d.weekday())
    result = {}
    for i in range(7):
        day = mon + timedelta(days=i)
        result[str(day)] = day_anomalies(day)
    return result


def recent_anomalies(d: date | None = None, days: int = 7) -> list[Anomaly]:
    """All anomalies in the last N days."""
    if d is None: d = date.today()
    all_anomalies = []
    for i in range(days):
        day = d - timedelta(days=i)
        da = day_anomalies(day)
        all_anomalies.extend(da.anomalies)
    return sorted(all_anomalies, key=lambda a: -abs(a.z_score))
