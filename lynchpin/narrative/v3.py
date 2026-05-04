"""V3 analytics query surface — precomputed behavioral analytics from GPT 5.5 Pro.

Import: from lynchpin.narrative.v3 import daily, archetypes, anomalies, trends, ...

This module reads from v3_analytics.duckdb (loaded from retrospective_deep_analysis_v3_bundle).
All metrics are active-hour-normalized against scaffold baseline data, covering 484 active days
from 2024-10-14 through 2026-04-23.

The v3 data supersedes several enrich/narrative modules:
  - enrich/metrics/attention.py  → transitions table
  - enrich/metrics/cross_source.py → correlations table
  - enrich/metrics/productivity.py → daily metrics + archetypes
  - enrich/patterns.py (classify_day) → day_archetype_summary
  - narrative/anomaly.py → anomaly_days
  - narrative/circadian.py → hour_profile
  - narrative/episode.py → episode_leaderboards
  - narrative/trends.py → monthly_metrics
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Sequence

import duckdb

DB_PATH = os.path.join(
    os.environ.get("LYNCHPIN_REPO_ROOT", "."),
    ".lynchpin/enrich/v3_analytics.duckdb",
)


def _db(read_only: bool = True):
    return duckdb.connect(DB_PATH, read_only=read_only)


# ── Result types ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DailyMetric:
    date: date
    active_hours: float
    # Productivity
    productive_h: float
    productive_ratio: float
    deep_candidate_h: float
    deep_ratio: float
    nsfw_h: float
    nsfw_ratio: float
    unproductive_h: float
    # Attention
    fragmentation: float
    activity_entropy_bits: float
    topic_entropy_bits: float
    switches_per_active_h: float
    # Top activities (from one-hot columns)
    top_activities: dict[str, float]
    top_topics: dict[str, float]
    top_macros: dict[str, float]
    # Health / context
    sleep_hours: float | None
    sleep_score: float | None
    stress_avg: float | None
    heart_rate_avg: float | None
    steps: int | None
    # Dev signals
    commit_count: int
    shell_commands: int
    ai_session_count: int
    lines_added: int
    lines_deleted: int
    # Classification
    archetype_label: str | None
    archetype_id: int | None


@dataclass(frozen=True)
class ArchetypeSummary:
    id: int
    label: str
    days: int
    active_hours_mean: float
    productive_ratio_mean: float
    deep_ratio_mean: float
    nsfw_ratio_mean: float
    fragmentation_mean: float
    top_activity_mix: list[dict]
    examples: list[str]


@dataclass(frozen=True)
class AnomalyDay:
    date: date
    outlier_score: float
    reasons: list[str]
    active_hours: float
    productive_ratio: float
    deep_ratio: float
    nsfw_ratio: float
    archetype_label: str | None


@dataclass(frozen=True)
class MonthlyTrend:
    month: str
    active_hours: float
    productive_h: float
    unproductive_h: float
    deep_candidate_h: float
    nsfw_h: float
    top_activity: str
    top_topic: str


@dataclass(frozen=True)
class TransitionRow:
    from_label: str
    to_label: str
    weight_h: float
    stickiness: float | None  # self-loop rate, only for activity_stickiness


@dataclass(frozen=True)
class CorrelationRow:
    x: str
    y: str
    pearson_r: float
    n: int


@dataclass(frozen=True)
class EpisodeRow:
    episode_id: str
    episode_label: str
    date: date
    duration_h: float
    dominant_activity: str
    dominant_topic: str
    productive_score: float
    deep_work_candidate: bool


@dataclass(frozen=True)
class CircadianProfile:
    hours: list[dict]  # [{hour, active, productive_ratio, nsfw_ratio, ...}]
    peak_productive_hour: int
    peak_active_hour: int


@dataclass(frozen=True)
class RecurringTitle:
    title_excerpt: str
    app: str
    activity: str
    total_hours: float
    row_count: int
    global_rank: int


# ── Daily metrics ───────────────────────────────────────────────────────────


def daily(d: date) -> DailyMetric | None:
    """Single day's full behavioral metrics."""
    db = _db()
    row = db.execute(
        "SELECT * FROM daily_metrics_v3 WHERE date = ?::DATE", [d.isoformat()]
    ).fetchone()
    db.close()
    if row is None:
        return None
    return _row_to_daily(row)


def daily_range(start: date, end: date, *, min_active_hours: float = 0.0) -> list[DailyMetric]:
    """All daily metrics in a date range, optionally filtering low-activity days."""
    db = _db()
    rows = db.execute("""
        SELECT * FROM daily_metrics_v3
        WHERE date >= ?::DATE AND date <= ?::DATE AND active_hours >= ?
        ORDER BY date
    """, [start.isoformat(), end.isoformat(), min_active_hours]).fetchall()
    db.close()
    return [_row_to_daily(r) for r in rows]


def active_days() -> list[DailyMetric]:
    """All days with active_hours > 0, post-2024-10-14."""
    db = _db()
    rows = db.execute("""
        SELECT * FROM daily_metrics_v3
        WHERE active_hours > 0 AND date >= '2024-10-14'
        ORDER BY date
    """).fetchall()
    db.close()
    return [_row_to_daily(r) for r in rows]


# ── Archetypes ──────────────────────────────────────────────────────────────


def archetypes() -> list[ArchetypeSummary]:
    """The 6 GPT-derived day archetypes with statistical profiles."""
    db = _db()
    rows = db.execute("""
        SELECT archetype_id, label, days, active_hours_mean,
               productive_ratio_mean, deep_ratio_mean, nsfw_ratio_mean,
               fragmentation_mean, top_activity_mix, date_examples
        FROM day_archetype_summary_v3
        ORDER BY days DESC
    """).fetchall()
    db.close()

    results = []
    for r in rows:
        import json
        mix = json.loads(r[8]) if r[8] else []
        examples = (r[9] or "").split("|")[:5] if r[9] else []
        results.append(ArchetypeSummary(
            id=r[0], label=r[1], days=r[2], active_hours_mean=r[3],
            productive_ratio_mean=r[4], deep_ratio_mean=r[5],
            nsfw_ratio_mean=r[6], fragmentation_mean=r[7],
            top_activity_mix=mix, examples=examples,
        ))
    return results


# ── Anomalies ───────────────────────────────────────────────────────────────


def anomalies(*, min_score: float = 0.0, limit: int = 50) -> list[AnomalyDay]:
    """Top anomalous days by multi-dimensional outlier score."""
    db = _db()
    rows = db.execute("""
        SELECT date, outlier_score, reasons, active_hours_x,
               productive_ratio, deep_ratio, nsfw_ratio, archetype_label
        FROM anomaly_days_top200_v3
        WHERE outlier_score >= ?
        ORDER BY outlier_score DESC
        LIMIT ?
    """, [min_score, limit]).fetchall()
    db.close()

    results = []
    for r in rows:
        reasons_list = [p.strip() for p in (r[2] or "").split(";") if p.strip()]
        results.append(AnomalyDay(
            date=r[0], outlier_score=r[1], reasons=reasons_list,
            active_hours=r[3] or 0, productive_ratio=r[4] or 0,
            deep_ratio=r[5] or 0, nsfw_ratio=r[6] or 0,
            archetype_label=r[7],
        ))
    return results


def anomaly_for_date(d: date) -> AnomalyDay | None:
    """Check if a specific day was anomalous."""
    db = _db()
    row = db.execute(
        "SELECT * FROM anomaly_days_top200_v3 WHERE date = ?::DATE", [d.isoformat()]
    ).fetchone()
    db.close()
    if row is None:
        return None
    reasons_list = [p.strip() for p in (row[2] or "").split(";") if p.strip()]
    return AnomalyDay(
        date=row[0], outlier_score=row[1], reasons=reasons_list,
        active_hours=row[3] or 0, productive_ratio=row[4] or 0,
        deep_ratio=row[5] or 0, nsfw_ratio=row[6] or 0,
        archetype_label=row[7],
    )


# ── Monthly trends ──────────────────────────────────────────────────────────


def monthly_trends() -> list[MonthlyTrend]:
    """Monthly arcs — active hours, productivity, NSFW, top activity/topic."""
    db = _db()
    rows = db.execute("""
        SELECT month, active_hours, productive_true_h, productive_false_h,
               deep_candidate_h, nsfw_h, top_activity, top_topic
        FROM monthly_metrics_v3
        ORDER BY month
    """).fetchall()
    db.close()
    return [MonthlyTrend(
        month=r[0], active_hours=r[1] or 0, productive_h=r[2] or 0,
        unproductive_h=r[3] or 0, deep_candidate_h=r[4] or 0,
        nsfw_h=r[5] or 0, top_activity=r[6] or "", top_topic=r[7] or "",
    ) for r in rows]


# ── Transitions ─────────────────────────────────────────────────────────────


def activity_transitions(*, top_n: int = 20) -> list[TransitionRow]:
    """Top activity→activity transition pairs by weighted hours."""
    db = _db()
    rows = db.execute("""
        SELECT from_activity, to_activity, from_weight_h
        FROM transition_activity_top1000_v3
        WHERE from_weight_h > 0
        ORDER BY from_weight_h DESC
        LIMIT ?
    """, [top_n]).fetchall()
    db.close()
    return [TransitionRow(from_label=r[0], to_label=r[1], weight_h=r[2] or 0, stickiness=None)
            for r in rows]


def activity_stickiness() -> list[TransitionRow]:
    """Self-loop rates per activity — how 'sticky' each activity is."""
    db = _db()
    rows = db.execute("""
        SELECT activity, outgoing_count, same_activity_count, self_loop_rate, from_weight_h
        FROM transition_activity_stickiness_v3
        ORDER BY from_weight_h DESC
    """).fetchall()
    db.close()
    return [TransitionRow(from_label=r[0], to_label=r[0],
                          weight_h=r[4], stickiness=r[3]) for r in rows]


# ── Episodes ────────────────────────────────────────────────────────────────


def episodes(*, kind: str = "productive", top_n: int = 20) -> list[EpisodeRow]:
    """Episode leaderboard. kind: productive, deep, longest, nsfw."""
    table_map = {
        "productive": "episode_leaderboard_productive_v3",
        "deep": "episode_leaderboard_deep_v3",
        "longest": "episode_leaderboard_longest_v3",
        "nsfw": "episode_leaderboard_nsfw_v3",
    }
    table = table_map.get(kind, table_map["productive"])
    db = _db()
    rows = db.execute(f"""
        SELECT episode_id, episode_label, date, duration_h,
               dominant_activity, dominant_topic, productive_ratio, deep_work_candidate_hours
        FROM "{table}"
        LIMIT ?
    """, [top_n]).fetchall()
    db.close()
    return [EpisodeRow(
        episode_id=r[0] or "", episode_label=r[1] or "",
        date=r[2], duration_h=r[3] or 0,
        dominant_activity=r[4] or "", dominant_topic=r[5] or "",
        productive_score=r[6] or 0, deep_work_candidate=bool(r[7] and r[7] > 0),
    ) for r in rows]


# ── Circadian ───────────────────────────────────────────────────────────────


def circadian() -> CircadianProfile:
    """Hourly activity profile with productive/NSFW ratios."""
    db = _db()
    rows = db.execute("""
        SELECT hour, labelled_h, productive_h, unproductive_h,
               nsfw_h, deep_h, productive_ratio, nsfw_ratio, deep_ratio
        FROM hour_profile_v3
        ORDER BY hour
    """).fetchall()
    db.close()

    hours = []
    peak_active_h = 0
    peak_active_val = 0
    peak_prod_h = 0
    peak_prod_val = 0

    # Column order: 0=hour, 1=labelled_h, 2=productive_h, 3=unproductive_h,
    # 4=nsfw_h, 5=deep_h, 6=productive_ratio, 7=nsfw_ratio, 8=deep_ratio
    for row_data in rows:
        h = int(row_data[0])
        labelled = row_data[1] or 0
        prod_ratio = row_data[6] or 0
        hours.append({
            "hour": h, "active": round(labelled, 2),
            "productive_h": round(row_data[2] or 0, 2),
            "unproductive_h": round(row_data[3] or 0, 2),
            "nsfw_h": round(row_data[4] or 0, 2),
            "deep_h": round(row_data[5] or 0, 2),
            "productive_ratio": round(prod_ratio, 3),
            "nsfw_ratio": round(row_data[7] or 0, 3),
            "deep_ratio": round(row_data[8] or 0, 3),
        })
        if labelled > peak_active_val:
            peak_active_val = labelled
            peak_active_h = h
        if prod_ratio > peak_prod_val:
            peak_prod_val = prod_ratio
            peak_prod_h = h

    return CircadianProfile(
        hours=hours,
        peak_productive_hour=peak_prod_h,
        peak_active_hour=peak_active_h,
    )


# ── Correlations ────────────────────────────────────────────────────────────


def correlations(*, min_abs_r: float = 0.2) -> list[CorrelationRow]:
    """Cross-source correlations (sleep, health, git, shell vs behavioral metrics)."""
    db = _db()
    rows = db.execute("""
        SELECT x, y, pearson_r, n
        FROM cross_source_correlations_v3
        WHERE ABS(pearson_r) >= ?
        ORDER BY ABS(pearson_r) DESC
    """, [min_abs_r]).fetchall()
    db.close()
    return [CorrelationRow(x=r[0], y=r[1], pearson_r=r[2], n=r[3]) for r in rows]


# ── Recurring titles ────────────────────────────────────────────────────────


def recurring_titles(*, top_n: int = 50) -> list[RecurringTitle]:
    """Most recurrent window titles — the attention anchors."""
    db = _db()
    rows = db.execute("""
        SELECT title, app_filled, activity_filled,
               weighted_h, rows, row_number() OVER (ORDER BY weighted_h DESC) as rn
        FROM title_recurrence_top2000_v3
        ORDER BY weighted_h DESC
        LIMIT ?
    """, [top_n]).fetchall()
    db.close()
    return [RecurringTitle(
        title_excerpt=(r[0] or "")[:120], app=r[1] or "",
        activity=r[2] or "", total_hours=round(r[3] or 0, 1),
        row_count=int(r[4] or 0), global_rank=int(r[5] or 0),
    ) for r in rows]


# ── Distributions ───────────────────────────────────────────────────────────


def activity_distribution() -> list[tuple[str, float, float]]:
    """Activity → hours + percent of total labelled time."""
    db = _db()
    rows = db.execute("""
        SELECT key, hours, percent
        FROM activity_distribution_v3
        ORDER BY hours DESC
    """).fetchall()
    db.close()
    return [(r[0], r[1] or 0, r[2] or 0) for r in rows]


def topic_distribution() -> list[tuple[str, float, float]]:
    """Topic → hours + percent."""
    db = _db()
    rows = db.execute("""
        SELECT key, hours, percent
        FROM topic_distribution_v3
        ORDER BY hours DESC
    """).fetchall()
    db.close()
    return [(r[0], r[1] or 0, r[2] or 0) for r in rows]


def macro_distribution() -> list[tuple[str, float, float]]:
    """Macro-mode → hours + percent (e.g. ai_work_chat, engineering, nsfw)."""
    db = _db()
    rows = db.execute("""
        SELECT key, hours, percent
        FROM macro_distribution_v3
        ORDER BY hours DESC
    """).fetchall()
    db.close()
    return [(r[0], r[1] or 0, r[2] or 0) for r in rows]


def weekday_profile() -> list[dict]:
    """Activity patterns by day of week."""
    db = _db()
    rows = db.execute("""
        SELECT weekday, labelled_h, productive_h, unproductive_h,
               nsfw_h, deep_h, productive_ratio
        FROM weekday_profile_v3
    """).fetchall()
    db.close()
    return [{
        "weekday": r[0], "labelled_h": round(r[1] or 0, 2),
        "productive_h": round(r[2] or 0, 2),
        "unproductive_h": round(r[3] or 0, 2),
        "nsfw_h": round(r[4] or 0, 2),
        "deep_h": round(r[5] or 0, 2),
        "productive_ratio": round(r[6] or 0, 3),
    } for r in rows]


# ── Overview ────────────────────────────────────────────────────────────────


def overview() -> dict:
    """High-level summary: scope, totals, key distributions."""
    db = _db()
    # Total labelled hours across all activities
    act_dist = db.execute("""
        SELECT key, hours FROM activity_distribution_v3 ORDER BY hours DESC
    """).fetchall()

    total_active = sum(r[1] for r in act_dist)

    # Productive split
    prod_true = db.execute("""
        SELECT hours FROM macro_distribution_v3 WHERE key = 'ai_work_chat'
    """).fetchone()
    eng_h = db.execute("""
        SELECT hours FROM macro_distribution_v3 WHERE key = 'engineering'
    """).fetchone()
    nsfw_h = db.execute("""
        SELECT hours FROM macro_distribution_v3 WHERE key = 'nsfw'
    """).fetchone()

    # Active days
    n_days = db.execute("""
        SELECT count(*) FROM daily_metrics_v3
        WHERE active_hours > 0 AND date >= '2024-10-14'
    """).fetchone()[0]

    # Date range
    dr = db.execute("""
        SELECT min(date), max(date) FROM daily_metrics_v3
        WHERE active_hours > 0
    """).fetchone()

    db.close()

    return {
        "active_days": n_days,
        "date_range": f"{dr[0]} → {dr[1]}",
        "total_labelled_hours": round(total_active, 1),
        "ai_work_chat_hours": round(prod_true[0] if prod_true else 0, 1),
        "engineering_hours": round(eng_h[0] if eng_h else 0, 1),
        "nsfw_hours": round(nsfw_h[0] if nsfw_h else 0, 1),
        "top_activities": [(r[0], round(r[1], 1)) for r in act_dist[:8]],
    }


# ── Helpers ─────────────────────────────────────────────────────────────────

_ACTIVITY_COLS = [c for c in [
    "activity_h__chatting_work", "activity_h__browsing_web",
    "activity_h__watching_video", "activity_h__coding",
    "activity_h__browsing_social", "activity_h__reading_article",
    "activity_h__writing", "activity_h__research",
    "activity_h__listening_music", "activity_h__email",
    "activity_h__file_management", "activity_h__reading_code",
    "activity_h__admin_task", "activity_h__reading_docs",
    "activity_h__chatting_social", "activity_h__code_review",
    "activity_h__system_task", "activity_h__shopping",
    "activity_h__planning", "activity_h__building",
    "activity_h__testing", "activity_h__configuring", "activity_h__idle",
]]

_TOPIC_COLS = [c for c in [
    "topic_h__ai_ml", "topic_h__unlabeled", "topic_h__nsfw",
    "topic_h__programming", "topic_h__entertainment",
    "topic_h__social_media", "topic_h__philosophy",
    "topic_h__self_improvement", "topic_h__music", "topic_h__other",
    "topic_h__admin", "topic_h__nix_os", "topic_h__science",
    "topic_h__health", "topic_h__news", "topic_h__psychology",
    "topic_h__relationships", "topic_h__gaming", "topic_h__career",
    "topic_h__personal_finance", "topic_h__rust",
]]

_MACRO_COLS = [c for c in [
    "macro_h__ai_work_chat", "macro_h__web_general",
    "macro_h__media_recovery", "macro_h__nsfw", "macro_h__engineering",
    "macro_h__learning_research_reading", "macro_h__social",
    "macro_h__writing_planning", "macro_h__ops_admin_system",
    "macro_h__other", "macro_h__idle",
]]


def _cols_to_dict(row: tuple, col_names: list[str], col_offset: int) -> dict[str, float]:
    """Extract {label: hours} from one-hot columns, stripping prefix."""
    result = {}
    for i, col in enumerate(col_names):
        val = row[col_offset + i]
        if val and val > 0:
            label = col.replace("activity_h__", "").replace("topic_h__", "").replace("macro_h__", "")
            result[label] = round(val, 2)
    return dict(sorted(result.items(), key=lambda x: -x[1])[:8])


def _row_to_daily(row: tuple) -> DailyMetric:
    """Map a daily_metrics_v3 row to DailyMetric."""
    # Column indices (from the CSV schema)
    d = row[0]  # date (DATE → date)
    if isinstance(d, str):
        d = date.fromisoformat(d)
    active_h = row[1] or 0

    # Activity one-hots start at col 30 (0-indexed)
    act_offset = 30
    acts = {}
    for i, name in enumerate(_ACTIVITY_COLS):
        v = row[act_offset + i]
        if v and v > 0:
            acts[name.replace("activity_h__", "")] = round(v, 2)

    # Topic one-hots: col 53
    topic_offset = 53
    topics = {}
    for i, name in enumerate(_TOPIC_COLS):
        v = row[topic_offset + i]
        if v and v > 0:
            topics[name.replace("topic_h__", "")] = round(v, 2)

    # Macro one-hots: col 74
    macro_offset = 74
    macros = {}
    for i, name in enumerate(_MACRO_COLS):
        v = row[macro_offset + i]
        if v and v > 0:
            macros[name.replace("macro_h__", "")] = round(v, 2)

    return DailyMetric(
        date=d,
        active_hours=round(active_h, 2),
        productive_h=round(row[103] or 0, 2),
        productive_ratio=round(row[115] or 0, 3),
        deep_candidate_h=round(row[106] or 0, 2),
        deep_ratio=round(row[117] or 0, 3),
        nsfw_h=round(row[107] or 0, 2),
        nsfw_ratio=round(row[118] or 0, 3),
        unproductive_h=round(row[104] or 0, 2),
        fragmentation=round(row[4] or 0, 3) if row[4] is not None else 0,
        activity_entropy_bits=round(row[122] or 0, 3),
        topic_entropy_bits=round(row[123] or 0, 3),
        switches_per_active_h=round(row[119] or 0, 1),
        top_activities=acts,
        top_topics=topics,
        top_macros=macros,
        sleep_hours=round(row[7] or 0, 1) if row[7] is not None else None,
        sleep_score=int(row[8]) if row[8] is not None else None,
        stress_avg=round(row[10] or 0, 1) if row[10] is not None else None,
        heart_rate_avg=round(row[11] or 0, 1) if row[11] is not None else None,
        steps=int(row[12]) if row[12] is not None else None,
        commit_count=int(row[16] or 0),
        shell_commands=int(row[15] or 0),
        ai_session_count=int(row[14] or 0),
        lines_added=int(row[19] or 0),
        lines_deleted=int(row[20] or 0),
        archetype_label=row[126] if len(row) > 126 else None,
        archetype_id=int(row[125]) if len(row) > 125 and row[125] is not None else None,
    )
