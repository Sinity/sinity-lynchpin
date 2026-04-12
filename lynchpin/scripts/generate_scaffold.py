"""Narrative scaffold generator: pre-compute evidence hierarchy for LLM narrative writing.

Queries all lynchpin source modules and writes a complete JSON scaffold at every
timescale (day → week → month → quarter → half → year → overview). The LLM reads
these files to write prose without needing live source queries.

Usage:
    python -m lynchpin.scripts.generate_scaffold                         # full dataset
    python -m lynchpin.scripts.generate_scaffold --day 2026-03-28        # single day
    python -m lynchpin.scripts.generate_scaffold --start 2026-03-01 --end 2026-03-28
    python -m lynchpin.scripts.generate_scaffold --overview-only
    python -m lynchpin.scripts.generate_scaffold --force                 # overwrite existing
    python -m lynchpin.scripts.generate_scaffold --dry-run               # show plan
"""

from __future__ import annotations

import argparse
import calendar
import shutil
import sys
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import logging

from ..core.config import get_config
from ..core.periods import (
    Period, parse_period, key_for_date, child_keys,
    period_keys_in_range, hierarchical_relpath, SCALE_ORDER,
)
from ..core.primitives import date_to_dt_range, logical_date, DAY_BOUNDARY_HOUR
from .scaffold_serialize import to_dict, write_json

# Suppress noisy cachew INFO logs during scaffold generation
logging.getLogger("cachew").setLevel(logging.WARNING)


# ══════════════════════════════════════════════════════════════════════════════
# Progress display
# ══════════════════════════════════════════════════════════════════════════════

class Progress:
    """Terminal progress bar with ETA and per-item timing."""

    BAR_WIDTH = 30
    SCALE_ICONS = {
        "day": "📅", "week": "📆", "month": "🗓",
        "quarter": "📊", "half": "📈", "year": "🗃",
        "overview": "🌐",
    }

    def __init__(self, scale: str, total: int):
        self.scale = scale
        self.total = total
        self.done = 0
        self.generated = 0
        self.skipped = 0
        self.failed = 0
        self.t0 = time.monotonic()
        self.item_times: list[float] = []
        self._last_item_start = self.t0
        icon = self.SCALE_ICONS.get(scale, "▸")
        print(f"\n{icon} {scale} — {total} item{'s' if total != 1 else ''}")

    def _bar(self) -> str:
        pct = self.done / self.total if self.total else 1
        filled = int(self.BAR_WIDTH * pct)
        bar = "█" * filled + "░" * (self.BAR_WIDTH - filled)
        return f"  [{bar}] {self.done}/{self.total}"

    def _eta(self) -> str:
        if not self.item_times:
            return ""
        avg = sum(self.item_times) / len(self.item_times)
        remaining = (self.total - self.done) * avg
        if remaining < 60:
            return f"~{remaining:.0f}s left"
        return f"~{remaining / 60:.1f}m left"

    def start_item(self, key: str):
        self._last_item_start = time.monotonic()

    def finish_item(self, key: str, *, status: str = "ok", elapsed: float | None = None):
        """Record item completion. status: 'ok', 'skip', 'fail'"""
        if elapsed is None:
            elapsed = time.monotonic() - self._last_item_start
        self.done += 1

        if status == "ok":
            self.generated += 1
            self.item_times.append(elapsed)
            eta = self._eta()
            print(f"\r{self._bar()} {key} ✓ {elapsed:.1f}s  {eta}   ", flush=True)
        elif status == "skip":
            self.skipped += 1
            print(f"\r{self._bar()} {key} ⊘ skip   ", flush=True)
        else:
            self.failed += 1
            print(f"\r{self._bar()} {key} ✗ {status}   ", flush=True)

    def summary(self) -> str:
        elapsed = time.monotonic() - self.t0
        parts = []
        if self.generated:
            parts.append(f"{self.generated} generated")
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        if self.failed:
            parts.append(f"{self.failed} failed")
        return f"  {' · '.join(parts)} ({elapsed:.1f}s)" if parts else ""


# ══════════════════════════════════════════════════════════════════════════════
# Dependency tracking
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class QueryDep:
    source: str
    function: str
    args: dict[str, Any]
    elapsed_s: float
    record_count: int | None


class DepTracker:
    """Track which source queries were made and how long they took."""

    def __init__(self):
        self.deps: list[QueryDep] = []
        self._start: float | None = None

    def start(self):
        self._start = time.monotonic()

    def record(self, source: str, function: str, args: dict[str, Any], result: Any):
        elapsed = time.monotonic() - self._start if self._start else 0.0
        count = None
        if isinstance(result, (list, tuple)):
            count = len(result)
        elif isinstance(result, dict):
            count = len(result)
        self.deps.append(QueryDep(source, function, args, elapsed, count))
        self._start = time.monotonic()  # reset for next

    def to_list(self) -> list[dict]:
        return [
            {
                "source": d.source, "function": d.function,
                "args": {k: str(v) for k, v in d.args.items()},
                "elapsed_s": round(d.elapsed_s, 3),
                "record_count": d.record_count,
            }
            for d in self.deps
        ]


def _track(tracker: DepTracker, source: str, fn_name: str, args: dict, fn, *a, **kw):
    """Call fn, track the dependency, return result."""
    tracker.start()
    result = fn(*a, **kw)
    # Materialize iterators
    if hasattr(result, '__next__'):
        result = list(result)
    tracker.record(source, fn_name, args, result)
    return result


# Substance access via proper source module
# See lynchpin/sources/substance.py for full API


def _build_features_verbose(start: date, end: date):
    """Wrapper around build_day_features that prints per-source progress."""
    from ..sources.patterns import _safe_fetch, DayFeatures
    from ..core.parse import iter_dates

    s_dt, e_dt = date_to_dt_range(start, end)

    def _load(label, fn, *args, **kwargs):
        print(f"      {label}...", end=" ", flush=True)
        t = time.monotonic()
        result = _safe(fn, *args, default=kwargs.pop('default', []), **kwargs)
        elapsed = time.monotonic() - t
        count = len(result) if isinstance(result, (list, dict)) else 0
        print(f"({count} records, {elapsed:.1f}s)")
        return result

    from ..sources.activitywatch import active_seconds_by_date, deep_work, sustained_focus, fragmentation, attention, app_sessions
    from ..sources.git import daily_activity
    from ..sources.terminal import shell_sessions
    from ..sources.polylogue import daily_activity as chat_daily
    from ..sources.sleep import entries as sleep_entries
    from ..sources.spotify import daily_listening
    from ..sources.reddit import daily_activity as reddit_daily
    from ..sources.health import daily_steps, daily_vitality, daily_health_summary
    from ..sources.web import daily_browsing
    from ..sources.exports import daily_messenger_activity, daily_raindrop_activity
    from ..sources.substance import daily_summary as substance_daily

    aw_active = _load("AW active", active_seconds_by_date, start, end, default={})
    dw_blocks = _load("AW deep work", deep_work, start=s_dt, end=e_dt)
    sf_blocks = _load("AW sustained focus", sustained_focus, start=s_dt, end=e_dt)
    frag_list = _load("AW fragmentation", fragmentation, start=start, end=end)
    att_list = _load("AW attention", attention, start=start, end=end)
    sessions = _load("AW sessions", app_sessions, start=s_dt, end=e_dt)
    git_act = _load("Git", daily_activity, start=start, end=end)
    shells = _load("Terminal", shell_sessions, start=s_dt, end=e_dt)
    chat_act = _load("Polylogue", chat_daily, start=start, end=end)
    sleep_data_raw = _load("Sleep", sleep_entries, default={})
    spotify_act = _load("Spotify", daily_listening, start=start, end=end)
    reddit_act = _load("Reddit", reddit_daily, start=start, end=end)
    steps_data = _load("Health steps", daily_steps, start=start, end=end)
    vitality_data = _load("Health vitality", daily_vitality, start=start, end=end)
    health_sum = _load("Health summary", daily_health_summary, start=start, end=end)
    web_act = _load("Web browsing", daily_browsing, start=start, end=end)
    msg_act = _load("Messenger", daily_messenger_activity, start=start, end=end)
    bm_act = _load("Raindrop", daily_raindrop_activity, start=start, end=end)
    sub_act = _load("Substance", substance_daily, start=start, end=end)

    # Aggregate into per-day maps
    dw_by_day: dict[date, float] = {}
    for b in dw_blocks:
        dw_by_day[b.start.date()] = dw_by_day.get(b.start.date(), 0) + b.duration_min
    sf_by_day: dict[date, float] = {}
    for b in sf_blocks:
        sf_by_day[b.start.date()] = sf_by_day.get(b.start.date(), 0) + b.duration_min
    frag_by_day = {f.date: f.fragmentation for f in frag_list}
    att_by_day = {a.date: a.project_count for a in att_list}
    git_by_day: dict[date, int] = {}
    for g in git_act:
        git_by_day[g.date] = git_by_day.get(g.date, 0) + g.commit_count
    shell_by_day: dict[date, int] = {}
    for s in shells:
        shell_by_day[s.start.date()] = shell_by_day.get(s.start.date(), 0) + s.command_count
    chat_by_day: dict[date, int] = {}
    for c in chat_act:
        chat_by_day[c.date] = chat_by_day.get(c.date, 0) + c.session_count
    sleep_data = {e.date: e for e in sleep_data_raw} if isinstance(sleep_data_raw, list) else sleep_data_raw
    spotify_by_day = {s.date: s.hours for s in spotify_act}
    reddit_by_day = {r.date: r.comment_count for r in reddit_act}
    steps_by_day = {s.date: s.steps for s in steps_data}
    vitality_by_day = {v.date: v.activity_score for v in vitality_data if v.activity_score is not None}
    health_by_day = {h.date: h for h in health_sum}
    web_by_day = {w.date: w for w in web_act}
    msg_by_day = {m.date: m.message_count for m in msg_act}
    bm_by_day = {r.date: r.bookmarks_added for r in bm_act}
    sub_by_day = {s.date: s.dose_count for s in sub_act}
    mode_by_day: dict[date, str] = {}
    proj_by_day: dict[date, str] = {}
    for sess in sessions:
        d = sess.start.date()
        if d not in mode_by_day and sess.mode:
            mode_by_day[d] = sess.mode
        if d not in proj_by_day and sess.project:
            proj_by_day[d] = sess.project

    # Collect all dates with data from any source
    data_dates: set[date] = set()
    if isinstance(aw_active, dict):
        data_dates.update(d for d, v in aw_active.items() if v > 0)
    data_dates.update(dw_by_day)
    data_dates.update(sf_by_day)
    data_dates.update(frag_by_day)
    data_dates.update(att_by_day)
    data_dates.update(git_by_day)
    data_dates.update(shell_by_day)
    data_dates.update(chat_by_day)
    data_dates.update(sleep_data)
    data_dates.update(spotify_by_day)
    data_dates.update(reddit_by_day)
    data_dates.update(steps_by_day)
    data_dates.update(vitality_by_day)
    data_dates.update(health_by_day)
    data_dates.update(web_by_day)
    data_dates.update(msg_by_day)
    data_dates.update(bm_by_day)
    data_dates.update(sub_by_day)
    data_dates.update(mode_by_day)

    print("      Assembling features...", end=" ", flush=True)
    result: list[DayFeatures] = []
    skipped = 0
    for d in iter_dates(start, end):
        if d not in data_dates:
            skipped += 1
            continue
        sleep_entry = sleep_data.get(d)
        health = health_by_day.get(d)
        web = web_by_day.get(d)
        aw_has_data = isinstance(aw_active, dict) and d in aw_active and aw_active[d] > 0
        result.append(DayFeatures(
            date=d,
            # AW — None if no AW data for this day
            active_hours=round(aw_active[d] / 3600, 2) if aw_has_data else None,
            deep_work_min=round(dw_by_day[d], 1) if d in dw_by_day else None,
            sustained_focus_min=round(sf_by_day[d], 1) if d in sf_by_day else None,
            fragmentation=round(frag_by_day[d], 3) if d in frag_by_day else None,
            project_count=att_by_day[d] if d in att_by_day else None,
            # Git
            commit_count=git_by_day[d] if d in git_by_day else None,
            # Terminal
            command_count=shell_by_day[d] if d in shell_by_day else None,
            # Polylogue
            chat_sessions=chat_by_day[d] if d in chat_by_day else None,
            # Sleep
            sleep_hours=round(sleep_entry.total_minutes / 60, 2) if sleep_entry else None,
            sleep_score=round(sleep_entry.avg_score, 1) if sleep_entry and sleep_entry.avg_score is not None else None,
            # Spotify
            listening_hours=round(spotify_by_day[d], 2) if d in spotify_by_day else None,
            # Reddit
            reddit_comments=reddit_by_day[d] if d in reddit_by_day else None,
            # Health basic
            daily_steps=steps_by_day[d] if d in steps_by_day else None,
            vitality_score=vitality_by_day[d] if d in vitality_by_day else None,
            # Health expanded — only if health summary exists for this date
            stress_avg=round(health.stress_avg, 1) if health and health.stress_avg is not None else None,
            heart_rate_avg=round(health.heart_rate_avg, 1) if health and health.heart_rate_avg is not None else None,
            heart_rate_resting=round(health.heart_rate_resting, 1) if health and health.heart_rate_resting is not None else None,
            hrv_rmssd=round(health.hrv_rmssd_avg, 2) if health and health.hrv_rmssd_avg is not None else None,
            spo2_avg=round(health.spo2_avg, 1) if health and health.spo2_avg is not None else None,
            floors_climbed=round(health.floors, 1) if health and health.floors is not None else None,
            skin_temp_avg=round(health.skin_temp_avg, 2) if health and health.skin_temp_avg is not None else None,
            snoring_duration_min=round(health.snoring_duration_s / 60, 1) if health and health.snoring_duration_s else None,
            # Web
            browsing_visits=web.visit_count if web else None,
            browsing_domains=web.unique_domains if web else None,
            # Social
            messenger_messages=msg_by_day[d] if d in msg_by_day else None,
            bookmarks_added=bm_by_day[d] if d in bm_by_day else None,
            # Substance
            substance_doses=sub_by_day[d] if d in sub_by_day else None,
            # Categorical
            dominant_mode=mode_by_day.get(d, "unknown"),
            dominant_project=proj_by_day.get(d, ""),
        ))
    print(f"done ({len(result)} days, {skipped} no-data skipped)")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Safe callers — catch individual source failures without aborting the scaffold
# ══════════════════════════════════════════════════════════════════════════════

def _safe(fn, *args, default=None, **kwargs):
    """Call fn; on exception return default and print warning."""
    try:
        result = fn(*args, **kwargs)
        if hasattr(result, '__next__'):
            result = list(result)
        return result
    except Exception as exc:
        print(f"  ⚠ {fn.__module__}.{fn.__qualname__}: {exc}", file=sys.stderr)
        return default


# ══════════════════════════════════════════════════════════════════════════════
# Baseline comparison
# ══════════════════════════════════════════════════════════════════════════════

def _baseline_comparison(d: date, metrics: dict[str, float], all_features: list | None = None) -> dict:
    """Compare today's metrics against 7-day and 30-day rolling averages.

    If all_features is provided (pre-computed DayFeatures for the full range),
    use them instead of re-querying build_day_features(). This avoids the
    catastrophic N×258K Spotify cache reload when generating 500+ days.
    """
    result = {}

    if all_features is not None:
        lookback_start = d - timedelta(days=30)
        features = [f for f in all_features if lookback_start <= f.date < d]
    else:
        from ..sources.patterns import build_day_features
        lookback_start = d - timedelta(days=30)
        features = _safe(build_day_features, lookback_start, d - timedelta(days=1), default=[])

    if not features:
        return result

    for metric_name, today_val in metrics.items():
        if today_val is None:
            continue
        vals_30 = [float(v) for f in features if hasattr(f, metric_name) and (v := getattr(f, metric_name)) is not None]
        vals_7 = vals_30[-7:] if len(vals_30) >= 7 else vals_30

        if not vals_30:
            continue

        avg_30 = sum(vals_30) / len(vals_30) if vals_30 else 0
        avg_7 = sum(vals_7) / len(vals_7) if vals_7 else 0

        # IQR-based anomaly detection
        sorted_vals = sorted(vals_30)
        n = len(sorted_vals)
        q1 = sorted_vals[n // 4] if n >= 4 else sorted_vals[0]
        q3 = sorted_vals[3 * n // 4] if n >= 4 else sorted_vals[-1]
        iqr = q3 - q1

        if iqr > 0:
            if today_val > q3 + 1.5 * iqr:
                flag = "anomalous_high"
            elif today_val < q1 - 1.5 * iqr:
                flag = "anomalous_low"
            elif today_val > avg_7 * 1.15:
                flag = "above"
            elif today_val < avg_7 * 0.85:
                flag = "below"
            else:
                flag = "typical"
        else:
            flag = "typical"

        result[metric_name] = {
            "today": round(today_val, 2),
            "avg_7d": round(avg_7, 2),
            "avg_30d": round(avg_30, 2),
            "flag": flag,
        }

    return result


# Transition bigrams via proper source module
# See lynchpin/sources/activity_segments.py: transition_bigrams()


# Regime detection via proper core module
# See lynchpin/core/analytics.py: detect_regimes()


# Correlation matrix via proper core module
# See lynchpin/core/analytics.py: correlation_matrix()


# ══════════════════════════════════════════════════════════════════════════════
# Batch source loader — load once, slice per day
# ══════════════════════════════════════════════════════════════════════════════

class BatchSources:
    """Pre-load all sources for a date range. generate_day() slices from this."""

    def __init__(self, start: date, end: date):
        from ..sources.activitywatch import (
            active_seconds_by_date, focus_spans, app_sessions,
            deep_work, fragmentation, attention, circadian,
            sustained_focus, daily_activity as aw_daily,
        )
        from ..sources.git import commit_facts, daily_activity as git_daily, commit_sessions
        from ..sources.polylogue import work_events, day_session_summaries
        from ..sources.terminal import shell_sessions
        from ..sources.sleep_infer import infer_sleep
        from ..sources.health import (
            daily_steps, daily_health_summary, heart_rate_measurements, daily_stress,
            calorie_burns, nap_sessions, activity_summaries, movement_records,
        )
        from ..sources.sleep import sleep_stages
        from ..sources.web import daily_browsing
        from ..sources.exports import daily_messenger_activity, daily_raindrop_activity
        from ..sources.timeline import work_sessions
        from ..sources.substance import entries as substance_entries

        s_dt, e_dt = date_to_dt_range(start, end)

        def _load(label, fn, *args, **kwargs):
            print(f"      {label}...", end=" ", flush=True)
            t = time.monotonic()
            result = _safe(fn, *args, default=kwargs.pop('default', []), **kwargs)
            elapsed = time.monotonic() - t
            count = len(result) if isinstance(result, (list, dict, tuple)) else 0
            print(f"({count}, {elapsed:.1f}s)")
            return result

        print("    Batch-loading sources...")
        # AW
        self.aw_active = _load("AW active", active_seconds_by_date, start, end, default={})
        self.focus_spans = _load("AW focus spans", focus_spans, start=s_dt, end=e_dt)
        self.app_sessions = _load("AW sessions", app_sessions, start=s_dt, end=e_dt)
        self.deep_work = _load("AW deep work", deep_work, start=s_dt, end=e_dt)
        self.sustained_focus = _load("AW sustained focus", sustained_focus, start=s_dt, end=e_dt)
        self.fragmentation = _load("AW fragmentation", fragmentation, start=start, end=end)
        self.attention = _load("AW attention", attention, start=start, end=end)
        self.circadian = _load("AW circadian", circadian, start=start, end=end)
        self.aw_daily = _load("AW daily", aw_daily, start=start, end=end)
        # Git
        self.git_facts = _load("Git facts", commit_facts, start=start, end=end)
        self.git_daily = _load("Git daily", git_daily, start=start, end=end)
        self.git_sessions = _load("Git sessions", commit_sessions, start=start, end=end)
        # Polylogue
        self.poly_events = _load("Polylogue events", work_events, start=start, end=end)
        self.poly_summaries = _load("Polylogue summaries", day_session_summaries, start=start, end=end)
        # Terminal
        self.shell_sessions = _load("Terminal", shell_sessions, start=s_dt, end=e_dt)
        # Sleep
        self.sleep = _load("Sleep", infer_sleep, start=start - timedelta(days=1), end=end)
        self.sleep_stages = _load("Sleep stages", sleep_stages, start=start, end=end)
        # Health
        self.steps = _load("Health steps", daily_steps, start=start, end=end)
        self.health_summary = _load("Health summary", daily_health_summary, start=start, end=end)
        self.hr = _load("Heart rate", heart_rate_measurements, start=start, end=end)
        self.stress = _load("Stress", daily_stress, start=start, end=end)
        self.calories = _load("Calories", calorie_burns, start=start, end=end)
        self.naps = _load("Naps", nap_sessions, start=start, end=end)
        self.activity_summary = _load("Activity summary", activity_summaries, start=start, end=end)
        self.movement = _load("Movement", movement_records, start=start, end=end)
        # Web
        self.browsing = _load("Web browsing", daily_browsing, start=start, end=end)
        # Social
        self.messenger = _load("Messenger", daily_messenger_activity, start=start, end=end)
        self.raindrop = _load("Raindrop", daily_raindrop_activity, start=start, end=end)
        # Timeline
        self.work_sessions = _load("Work sessions", work_sessions, start=start, end=end)
        # Substance
        self.substance = _load("Substance", substance_entries)

        # Build per-day indexes
        self._index_by_day()
        print(f"    → Batch load complete\n")

    def _index_by_day(self):
        """Build date→list indexes for fast per-day slicing."""
        self._frag_by_date = {f.date: f for f in self.fragmentation}
        self._attn_by_date = {a.date: a for a in self.attention}
        self._circ_by_date = _group_by_date(self.circadian, lambda c: c.date)
        self._git_daily_by_date = _group_by_date(self.git_daily, lambda g: g.date)
        self._git_facts_by_date = _group_by_date(self.git_facts, lambda f: f.authored_at.date() if hasattr(f, 'authored_at') and f.authored_at else None)
        self._git_sessions_by_date = _group_by_date(self.git_sessions, lambda s: s.start.date() if hasattr(s, 'start') and s.start else None)
        self._focus_by_date = _group_by_date(self.focus_spans, lambda s: s.start.date() if hasattr(s, 'start') and s.start else None)
        self._dw_by_date = _group_by_date(self.deep_work, lambda b: b.start.date() if hasattr(b, 'start') and b.start else None)
        self._sf_by_date = _group_by_date(self.sustained_focus, lambda b: b.start.date() if hasattr(b, 'start') and b.start else None)
        self._poly_events_by_date = _group_by_date(self.poly_events, lambda e: e.start.date() if hasattr(e, 'start') and e.start else None)
        self._poly_summaries_by_date = _group_by_date(self.poly_summaries, lambda s: s.date if hasattr(s, 'date') else None)
        self._shells_by_date = _group_by_date(self.shell_sessions, lambda s: s.start.date() if hasattr(s, 'start') and s.start else None)
        self._sleep_by_date = _group_by_date(self.sleep, lambda s: s.date if hasattr(s, 'date') else None)
        self._steps_by_date = {s.date: s for s in self.steps}
        self._health_by_date = {h.date: h for h in self.health_summary}
        self._hr_by_date = _group_by_date(self.hr, lambda h: h.timestamp.date() if hasattr(h, 'timestamp') and h.timestamp else None)
        self._stress_by_date = {s.date: s for s in self.stress}
        self._browsing_by_date = {b.date: b for b in self.browsing}
        self._messenger_by_date = {m.date: m for m in self.messenger}
        self._raindrop_by_date = {r.date: r for r in self.raindrop}
        self._work_sessions_by_date = _group_by_date(self.work_sessions, lambda s: s.start.date() if hasattr(s, 'start') and s.start else None)
        self._substance_by_date: dict[date, list] = defaultdict(list)
        for e in self.substance:
            self._substance_by_date[e.date].append(e)
        self._sleep_stages_by_date = _group_by_date(self.sleep_stages, lambda s: s.start.date() if hasattr(s, 'start') and s.start else None)
        self._calories_by_date = {c.date: c for c in self.calories}
        self._naps_by_date = _group_by_date(self.naps, lambda n: n.start.date() if hasattr(n, 'start') and n.start else None)
        self._activity_summary_by_date = {a.date: a for a in self.activity_summary}
        self._movement_by_date = _group_by_date(self.movement, lambda m: m.start.date() if hasattr(m, 'start') and m.start else None)


def _group_by_date(items: list, date_fn) -> dict[date, list]:
    """Group items by date using a date extraction function."""
    result: dict[date, list] = defaultdict(list)
    for item in items:
        d = date_fn(item)
        if d is not None:
            result[d].append(item)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Day scaffold
# ══════════════════════════════════════════════════════════════════════════════

def generate_day(d: date, output: Path, *, force: bool = False, all_features: list | None = None,
                 batch: BatchSources | None = None) -> bool:
    """Generate scaffold for a single day. Returns True if generated.

    If batch is provided, slices data from pre-loaded sources (fast).
    Otherwise, queries each source individually (slow, for single-day runs).
    """
    day_dir = _day_dir(d, output)
    if (day_dir / "manifest.json").exists() and not force:
        return False

    t0 = time.monotonic()

    if batch:
        # Fast path: slice from batch-loaded data
        active_h = batch.aw_active.get(d, 0) / 3600 if isinstance(batch.aw_active, dict) else 0
        frag = [batch._frag_by_date[d]] if d in batch._frag_by_date else []
        attn = [batch._attn_by_date[d]] if d in batch._attn_by_date else []
        circ = batch._circ_by_date.get(d, [])
        git_act = batch._git_daily_by_date.get(d, [])
        dw = batch._dw_by_date.get(d, [])
        sf = batch._sf_by_date.get(d, [])
        spans = batch._focus_by_date.get(d, [])
        facts = batch._git_facts_by_date.get(d, [])
        sessions = batch._git_sessions_by_date.get(d, [])
        poly_events = batch._poly_events_by_date.get(d, [])
        poly_summaries = batch._poly_summaries_by_date.get(d, [])
        shells = batch._shells_by_date.get(d, [])
        sleep_data = batch._sleep_by_date.get(d, [])
        steps = [batch._steps_by_date[d]] if d in batch._steps_by_date else []
        health_summary = [batch._health_by_date[d]] if d in batch._health_by_date else []
        hr_measurements = batch._hr_by_date.get(d, [])
        stress_measurements = [batch._stress_by_date[d]] if d in batch._stress_by_date else []
        browsing = [batch._browsing_by_date[d]] if d in batch._browsing_by_date else []
        messenger = [batch._messenger_by_date[d]] if d in batch._messenger_by_date else []
        raindrop = [batch._raindrop_by_date[d]] if d in batch._raindrop_by_date else []
        work_sess = batch._work_sessions_by_date.get(d, [])
        substance_day = batch._substance_by_date.get(d, [])
        sleep_stages_day = batch._sleep_stages_by_date.get(d, [])
        calories_day = [batch._calories_by_date[d]] if d in batch._calories_by_date else []
        naps_day = batch._naps_by_date.get(d, [])
        activity_summary_day = [batch._activity_summary_by_date[d]] if d in batch._activity_summary_by_date else []
        movement_day = batch._movement_by_date.get(d, [])
        # Per-day only sources (can't batch)
        from ..sources.activity_segments import segment_day
        from ..sources.day_summary import day_summary
        seg = _safe(segment_day, d, default=None)
        two_track = _safe(day_summary, d, default=None)
    else:
        # Slow path: query each source individually
        from ..sources.activitywatch import (
            active_seconds_by_date, focus_spans, app_sessions,
            deep_work, fragmentation, attention, circadian,
            sustained_focus, daily_activity as aw_daily,
        )
        from ..sources.activity_segments import segment_day
        from ..sources.git import commit_facts, daily_activity as git_daily, commit_sessions
        from ..sources.polylogue import work_events, day_session_summaries
        from ..sources.terminal import shell_sessions
        from ..sources.sleep_infer import infer_sleep
        from ..sources.sleep import sleep_stages as sleep_stages_fn
        from ..sources.health import (
            daily_steps, daily_health_summary, heart_rate_measurements, daily_stress,
            calorie_burns, nap_sessions, activity_summaries, movement_records,
        )
        from ..sources.web import daily_browsing
        from ..sources.exports import daily_messenger_activity, daily_raindrop_activity
        from ..sources.timeline import work_sessions as ws_fn
        from ..sources.day_summary import day_summary
        from ..sources.substance import entries_for_date as substance_for_date

        s_dt, e_dt = date_to_dt_range(d, d)
        active_secs = _safe(active_seconds_by_date, d, d, default={})
        active_h = active_secs.get(d, 0) / 3600 if active_secs else 0
        frag = _safe(fragmentation, start=d, end=d, default=[])
        attn = _safe(attention, start=d, end=d, default=[])
        circ = _safe(circadian, start=d, end=d, default=[])
        git_act = _safe(git_daily, start=d, end=d, default=[])
        dw = _safe(deep_work, start=s_dt, end=e_dt, default=[])
        sf = _safe(sustained_focus, start=s_dt, end=e_dt, default=[])
        spans = _safe(focus_spans, start=s_dt, end=e_dt, default=[])
        facts = _safe(commit_facts, start=d, end=d, default=[])
        sessions = _safe(commit_sessions, start=d, end=d, default=[])
        poly_events = _safe(work_events, start=d, end=d, default=[])
        poly_summaries = _safe(day_session_summaries, start=d, end=d, default=[])
        shells = _safe(shell_sessions, start=s_dt, end=e_dt, default=[])
        sleep_data = _safe(infer_sleep, start=d - timedelta(days=1), end=d, default=[])
        steps = _safe(daily_steps, start=d, end=d, default=[])
        health_summary = _safe(daily_health_summary, start=d, end=d, default=[])
        hr_measurements = _safe(heart_rate_measurements, start=d, end=d, default=[])
        stress_measurements = _safe(daily_stress, start=d, end=d, default=[])
        browsing = _safe(daily_browsing, start=d, end=d, default=[])
        messenger = _safe(daily_messenger_activity, start=d, end=d, default=[])
        raindrop = _safe(daily_raindrop_activity, start=d, end=d, default=[])
        work_sess = _safe(ws_fn, start=d, end=d, default=[])
        substance_day = _safe(substance_for_date, d, default=[])
        sleep_stages_day = _safe(sleep_stages_fn, start=d, end=d, default=[])
        calories_day = _safe(calorie_burns, start=d, end=d, default=[])
        naps_day = _safe(nap_sessions, start=d, end=d, default=[])
        activity_summary_day = _safe(activity_summaries, start=d, end=d, default=[])
        movement_day = _safe(movement_records, start=d, end=d, default=[])
        seg = _safe(segment_day, d, default=None)
        two_track = _safe(day_summary, d, default=None)

    total_commits = sum(g.commit_count for g in git_act) if git_act else 0
    total_churn = sum(getattr(g, 'lines_added', 0) + getattr(g, 'lines_deleted', 0) for g in git_act) if git_act else 0
    segments_data = to_dict(seg) if seg else None

    metrics = {
        "date": d.isoformat(),
        "active_hours": round(active_h, 2) if active_h else None,
        "deep_work_blocks": len(dw) if dw else None,
        "deep_work_min": round(sum(b.duration_min for b in dw), 1) if dw else None,
        "sustained_focus_min": round(sum(b.duration_min for b in sf), 1) if sf else None,
        "fragmentation": round(frag[0].fragmentation, 3) if frag else None,
        "attention_entropy": round(attn[0].entropy, 3) if attn else None,
        "commits": total_commits if total_commits else None,
        "churn": total_churn if total_churn else None,
    }

    # ── Baseline comparison ──
    baseline_metrics = {
        "active_hours": active_h if active_h else None,
        "commit_count": total_commits if total_commits else None,
        "fragmentation": frag[0].fragmentation if frag else None,
    }
    baseline = _safe(_baseline_comparison, d, baseline_metrics, all_features, default={})

    # ── Write files ──
    day_dir.mkdir(parents=True, exist_ok=True)

    write_json(day_dir / "metrics.json", metrics)
    write_json(day_dir / "focus_spans.json", spans)
    if segments_data:
        write_json(day_dir / "segments.json", segments_data)
    write_json(day_dir / "commits.json", {
        "facts": facts,
        "sessions": sessions,
        "daily": git_act,
    })
    write_json(day_dir / "ai_activity.json", {
        "work_events": poly_events,
        "session_summaries": poly_summaries,
    })
    write_json(day_dir / "shell.json", shells)
    write_json(day_dir / "sleep.json", sleep_data)
    if sleep_stages_day:
        write_json(day_dir / "sleep_stages.json", sleep_stages_day)
        # Compute per-night architecture from stages
        from ..sources.sleep import sleep_architecture
        arch = _safe(sleep_architecture, start=d, end=d, default=[])
        if arch:
            write_json(day_dir / "sleep_architecture.json", arch)
    write_json(day_dir / "health.json", {
        "steps": steps,
        "summary": health_summary,
        "heart_rate": hr_measurements,
        "stress": stress_measurements,
        "circadian": circ,
        "fragmentation": frag,
        "attention": attn,
        "deep_work": dw,
        "sustained_focus": sf,
        "calories": calories_day,
        "naps": naps_day,
        "activity_summary": activity_summary_day,
        "movement": movement_day,
    })
    if browsing:
        write_json(day_dir / "browsing.json", browsing)
    if messenger or raindrop:
        write_json(day_dir / "social.json", {"messenger": messenger, "raindrop": raindrop})
    if work_sess:
        write_json(day_dir / "work_sessions.json", work_sess)
    if substance_day:
        write_json(day_dir / "substance.json", substance_day)
    if two_track:
        write_json(day_dir / "two_track.json", two_track)
    write_json(day_dir / "baseline.json", baseline)

    # Manifest
    elapsed = round(time.monotonic() - t0, 2)
    write_json(day_dir / "manifest.json", {
        "scale": "day",
        "key": d.isoformat(),
        "generated_at": datetime.now().isoformat(),
        "elapsed_s": elapsed,
        "files": sorted(p.name for p in day_dir.iterdir() if p.suffix == ".json"),
    })

    return True


# ══════════════════════════════════════════════════════════════════════════════
# Week scaffold
# ══════════════════════════════════════════════════════════════════════════════

def generate_week(week_key: str, output: Path, *, force: bool = False,
                  all_features: list | None = None, batch: BatchSources | None = None) -> bool:
    period = parse_period("week", week_key)
    if period is None:
        return False

    week_dir = _week_dir(week_key, period, output)
    if (week_dir / "manifest.json").exists() and not force:
        return False

    t0 = time.monotonic()

    s, e = period.start, period.end

    if batch and all_features:
        # Fast path: slice from batch
        active_map = {d: v for d, v in batch.aw_active.items() if s <= d <= e} if isinstance(batch.aw_active, dict) else {}
        git_act = [g for g in batch.git_daily if s <= g.date <= e]
        git_facts = [f for f in batch.git_facts if hasattr(f, 'authored_at') and f.authored_at and s <= f.authored_at.date() <= e]
        poly_summaries = [p for p in batch.poly_summaries if hasattr(p, 'date') and s <= p.date <= e]
        poly_events = [p for p in batch.poly_events if hasattr(p, 'start') and p.start and s <= p.start.date() <= e]
        sleep = [sl for sl in batch.sleep if hasattr(sl, 'date') and s <= sl.date <= e]
        health = [h for h in batch.health_summary if s <= h.date <= e]
        browsing = [b for b in batch.browsing if s <= b.date <= e]
        features = [f for f in all_features if s <= f.date <= e]
    else:
        # Slow path
        from ..sources.activitywatch import active_seconds_by_date
        from ..sources.git import daily_activity as git_daily, commit_facts
        from ..sources.polylogue import day_session_summaries, work_events
        from ..sources.sleep import entries_in_range as sleep_range
        from ..sources.health import daily_health_summary
        from ..sources.web import daily_browsing
        from ..sources.patterns import build_day_features
        active_map = _safe(active_seconds_by_date, s, e, default={})
        git_act = _safe(git_daily, start=s, end=e, default=[])
        git_facts = _safe(commit_facts, start=s, end=e, default=[])
        poly_summaries = _safe(day_session_summaries, start=s, end=e, default=[])
        poly_events = _safe(work_events, start=s, end=e, default=[])
        sleep = _safe(sleep_range, s, e, default=[])
        health = _safe(daily_health_summary, start=s, end=e, default=[])
        browsing = _safe(daily_browsing, start=s, end=e, default=[])
        features = _safe(build_day_features, s, e, default=[])

    from ..sources.patterns import weekly_rhythm, activity_trends
    rhythm = _safe(weekly_rhythm, features, default=None) if features else None
    trends = _safe(activity_trends, features, default={}) if features else {}

    # These are cheap per-week computations, keep as direct calls
    from ..sources.intraday import clock_hour_profile
    from ..sources.activity_segments import segment_range, transition_bigrams
    hourly = _safe(clock_hour_profile, start=s, end=e, default=[])
    segs = _safe(segment_range, start=s, end=e, default=[])
    transitions = _safe(transition_bigrams, segs, default=None)

    # Day type clustering
    from ..sources.patterns import day_type_clusters
    clusters = _safe(day_type_clusters, features, default=[]) if len(features) >= 3 else []

    # Sleep × activity
    sleep_activity = []
    if sleep and features:
        sleep_by_date = {sl.date if hasattr(sl, 'date') else None: sl for sl in sleep}
        for f in features:
            sl = sleep_by_date.get(f.date)
            if sl and f.active_hours is not None:
                sleep_activity.append({
                    "date": f.date.isoformat(),
                    "sleep_hours": round(getattr(sl, 'total_minutes', 0) / 60, 2),
                    "next_day_active_h": round(f.active_hours, 2),
                    "next_day_fragmentation": round(f.fragmentation, 3) if f.fragmentation is not None else None,
                })

    # Per-day metrics table
    day_metrics = []
    for f in (features or []):
        day_metrics.append({
            "date": f.date.isoformat(),
            "active_hours": round(f.active_hours, 2) if f.active_hours is not None else None,
            "deep_work_min": round(f.deep_work_min, 1) if f.deep_work_min is not None else None,
            "commits": f.commit_count,
            "fragmentation": round(f.fragmentation, 3) if f.fragmentation is not None else None,
            "sleep_hours": round(f.sleep_hours, 2) if f.sleep_hours is not None else None,
            "chat_sessions": f.chat_sessions,
            "dominant_project": f.dominant_project,
            "dominant_mode": f.dominant_mode,
        })

    # Project breakdown
    project_commits: Counter = Counter()
    for fact in (git_facts or []):
        project_commits[getattr(fact, 'repo', 'unknown')] += 1

    # Work event kind distribution
    kind_dist: Counter = Counter()
    for ev in (poly_events or []):
        for k in (getattr(ev, 'kinds', ()) or ()):
            kind_dist[k] += 1

    # ── Write ──
    week_dir.mkdir(parents=True, exist_ok=True)

    # Health aggregation for week
    health_week = {}
    if health:
        stress_vals = [h.stress_avg for h in health if h.stress_avg is not None]
        hr_vals = [h.heart_rate_avg for h in health if h.heart_rate_avg is not None]
        cal_vals = [h.calories for h in health if h.calories is not None]
        if stress_vals:
            health_week["avg_stress"] = round(sum(stress_vals) / len(stress_vals), 1)
        if hr_vals:
            health_week["avg_heart_rate"] = round(sum(hr_vals) / len(hr_vals), 1)
        if cal_vals:
            health_week["avg_daily_calories"] = round(sum(cal_vals) / len(cal_vals), 0)

    # Browsing aggregation for week
    browsing_week = {}
    if browsing:
        browsing_week["total_visits"] = sum(b.visit_count for b in browsing)
        all_domains = set()
        for b in browsing:
            all_domains.update(d for d, _ in b.top_domains)
        browsing_week["unique_domains"] = len(all_domains)

    write_json(week_dir / "week_metrics.json", {
        "week": week_key,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "per_day": day_metrics,
        "total_active_hours": round(sum(v / 3600 for v in (active_map or {}).values()), 2),
        "total_commits": sum(g.commit_count for g in git_act) if git_act else 0,
        "project_commits": dict(project_commits.most_common()),
        "work_event_kinds": dict(kind_dist.most_common()),
        "clusters": clusters,
        "sleep_activity": sleep_activity,
        "health": health_week,
        "browsing": browsing_week,
    })
    write_json(week_dir / "week_transitions.json", transitions)
    write_json(week_dir / "week_intraday.json", hourly)
    if rhythm:
        write_json(week_dir / "week_rhythm.json", rhythm)
    if trends:
        write_json(week_dir / "week_trends.json", trends)

    elapsed = round(time.monotonic() - t0, 2)
    write_json(week_dir / "manifest.json", {
        "scale": "week",
        "key": week_key,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "generated_at": datetime.now().isoformat(),
        "elapsed_s": elapsed,
        "files": sorted(p.name for p in week_dir.iterdir() if p.suffix == ".json"),
    })

    return True


# ══════════════════════════════════════════════════════════════════════════════
# Month scaffold
# ══════════════════════════════════════════════════════════════════════════════

def generate_month(month_key: str, output: Path, *, force: bool = False,
                   all_features: list | None = None, batch: BatchSources | None = None) -> bool:
    period = parse_period("month", month_key)
    if period is None:
        return False

    month_name = calendar.month_name[period.start.month]
    month_dir = _month_dir(month_key, period, output)

    if (month_dir / "manifest.json").exists() and not force:
        return False

    t0 = time.monotonic()

    s, e = period.start, period.end

    if batch and all_features:
        # Fast path: slice from batch
        active_map = {d: v for d, v in batch.aw_active.items() if s <= d <= e} if isinstance(batch.aw_active, dict) else {}
        git_act = [g for g in batch.git_daily if s <= g.date <= e]
        poly_summaries = [p for p in batch.poly_summaries if hasattr(p, 'date') and s <= p.date <= e]
        poly_events = [p for p in batch.poly_events if hasattr(p, 'start') and p.start and s <= p.start.date() <= e]
        sleep = [sl for sl in batch.sleep if hasattr(sl, 'date') and s <= sl.date <= e]
        steps = [st for st in batch.steps if s <= st.date <= e]
        health = [h for h in batch.health_summary if s <= h.date <= e]
        browsing = [b for b in batch.browsing if s <= b.date <= e]
        features = [f for f in all_features if s <= f.date <= e]
    else:
        from ..sources.activitywatch import active_seconds_by_date
        from ..sources.git import daily_activity as git_daily
        from ..sources.polylogue import day_session_summaries, work_events
        from ..sources.sleep import entries_in_range as sleep_range
        from ..sources.health import daily_steps, daily_health_summary
        from ..sources.web import daily_browsing
        from ..sources.patterns import build_day_features
        active_map = _safe(active_seconds_by_date, s, e, default={})
        git_act = _safe(git_daily, start=s, end=e, default=[])
        poly_summaries = _safe(day_session_summaries, start=s, end=e, default=[])
        poly_events = _safe(work_events, start=s, end=e, default=[])
        sleep = _safe(sleep_range, s, e, default=[])
        steps = _safe(daily_steps, start=s, end=e, default=[])
        health = _safe(daily_health_summary, start=s, end=e, default=[])
        browsing = _safe(daily_browsing, start=s, end=e, default=[])
        features = _safe(build_day_features, s, e, default=[])

    # Run analytics on the pre-sliced features (no source re-query)
    from ..sources.patterns import FullAnalysis, weekly_rhythm, productivity_drivers, work_regime_changes, day_type_clusters, activity_trends, day_anomalies
    analysis = None
    if features:
        analysis = FullAnalysis(
            features=features,
            rhythm=_safe(weekly_rhythm, features, default=None),
            drivers=_safe(productivity_drivers, features, default=[]),
            regime_changes=_safe(work_regime_changes, features, default=[]),
            clusters=_safe(day_type_clusters, features, default=[]),
            trends=_safe(activity_trends, features, default={}),
            anomalies=_safe(day_anomalies, features, default={}),
        )

    # Transitions (per-day AW queries, can't batch easily)
    from ..sources.activity_segments import segment_range, transition_bigrams
    segs = _safe(segment_range, start=s, end=e, default=[])
    transitions = _safe(transition_bigrams, segs, default=None)

    # Substance
    from ..sources.substance import monthly_summary as substance_monthly
    substance_summary = _safe(substance_monthly, start=s, end=e, default=[])

    # Per-day metrics
    day_metrics = []
    if analysis and analysis.features:
        for f in analysis.features:
            day_metrics.append({
                "date": f.date.isoformat(),
                "active_hours": round(f.active_hours, 2) if f.active_hours is not None else None,
                "deep_work_min": round(f.deep_work_min, 1) if f.deep_work_min is not None else None,
                "commits": f.commit_count,
                "fragmentation": round(f.fragmentation, 3) if f.fragmentation is not None else None,
                "sleep_hours": round(f.sleep_hours, 2) if f.sleep_hours is not None else None,
                "daily_steps": f.daily_steps,
                "dominant_project": f.dominant_project,
            })

    # Weekly breakdown
    week_keys = child_keys("month", month_key)
    per_week_commits: dict[str, int] = {}
    for wk in week_keys:
        wp = parse_period("week", wk)
        if wp:
            per_week_commits[wk] = sum(
                g.commit_count for g in (git_act or [])
                if wp.start <= g.date <= wp.end
            )

    # Project dominance per week
    project_by_week: dict[str, Counter] = defaultdict(Counter)
    for g in (git_act or []):
        wk = key_for_date("week", g.date)
        project_by_week[wk][g.repo] += g.commit_count

    # ── Write ──
    month_dir.mkdir(parents=True, exist_ok=True)

    # Health aggregation for month
    health_month = {}
    if health:
        stress_vals = [h.stress_avg for h in health if h.stress_avg is not None]
        hr_vals = [h.heart_rate_avg for h in health if h.heart_rate_avg is not None]
        if stress_vals:
            health_month["avg_stress"] = round(sum(stress_vals) / len(stress_vals), 1)
        if hr_vals:
            health_month["avg_heart_rate"] = round(sum(hr_vals) / len(hr_vals), 1)

    # Browsing aggregation for month
    browsing_month = {}
    if browsing:
        browsing_month["total_visits"] = sum(b.visit_count for b in browsing)
        all_domains = set()
        for b in browsing:
            all_domains.update(d for d, _ in b.top_domains)
        browsing_month["unique_domains"] = len(all_domains)

    write_json(month_dir / "month_metrics.json", {
        "month": month_key,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "per_day": day_metrics,
        "per_week_commits": per_week_commits,
        "project_by_week": {wk: dict(c.most_common()) for wk, c in project_by_week.items()},
        "total_active_hours": round(sum(v / 3600 for v in (active_map or {}).values()), 2),
        "total_commits": sum(g.commit_count for g in git_act) if git_act else 0,
        "health": health_month,
        "browsing": browsing_month,
    })

    if analysis:
        write_json(month_dir / "month_patterns.json", {
            "rhythm": analysis.rhythm,
            "drivers": analysis.drivers,
            "clusters": analysis.clusters,
            "trends": analysis.trends,
            "anomalies": analysis.anomalies,
            "regime_changes": analysis.regime_changes,
        })

    write_json(month_dir / "month_transitions.json", transitions)

    if substance_summary:
        write_json(month_dir / "month_substance.json", substance_summary)

    elapsed = round(time.monotonic() - t0, 2)
    write_json(month_dir / "manifest.json", {
        "scale": "month",
        "key": month_key,
        "month_name": month_name,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "generated_at": datetime.now().isoformat(),
        "elapsed_s": elapsed,
        "files": sorted(p.name for p in month_dir.iterdir() if p.suffix == ".json"),
    })

    return True


# ══════════════════════════════════════════════════════════════════════════════
# Quarter scaffold
# ══════════════════════════════════════════════════════════════════════════════

def generate_quarter(quarter_key: str, output: Path, *, force: bool = False,
                     all_features: list | None = None, batch: BatchSources | None = None) -> bool:
    period = parse_period("quarter", quarter_key)
    if period is None:
        return False

    q_dir = _quarter_dir(quarter_key, period, output)
    if (q_dir / "manifest.json").exists() and not force:
        return False

    t0 = time.monotonic()

    from ..sources.patterns import activity_trends, work_regime_changes

    s, e = period.start, period.end

    if batch and all_features:
        features = [f for f in all_features if s <= f.date <= e]
        git_act = [g for g in batch.git_daily if s <= g.date <= e]
        active_map = {d: v for d, v in batch.aw_active.items() if s <= d <= e} if isinstance(batch.aw_active, dict) else {}
    else:
        from ..sources.patterns import build_day_features
        from ..sources.git import daily_activity as git_daily
        from ..sources.activitywatch import active_seconds_by_date
        features = _safe(build_day_features, s, e, default=[])
        git_act = _safe(git_daily, start=s, end=e, default=[])
        active_map = _safe(active_seconds_by_date, s, e, default={})

    trends = _safe(activity_trends, features, default={}) if features else {}
    regimes = _safe(work_regime_changes, features, default=[]) if features else []

    # Per-month summary
    month_keys = child_keys("quarter", quarter_key)
    per_month = []
    for mk in month_keys:
        mp = parse_period("month", mk)
        if not mp:
            continue
        month_active = sum(v / 3600 for d, v in (active_map or {}).items() if mp.start <= d <= mp.end)
        month_commits = sum(g.commit_count for g in (git_act or []) if mp.start <= g.date <= mp.end)
        per_month.append({
            "month": mk,
            "active_hours": round(month_active, 2),
            "commits": month_commits,
        })

    # Project arcs (per-month per-project commits)
    project_arcs: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for g in (git_act or []):
        mk = key_for_date("month", g.date)
        project_arcs[g.repo][mk] += g.commit_count

    q_dir.mkdir(parents=True, exist_ok=True)

    write_json(q_dir / "quarter_metrics.json", {
        "quarter": quarter_key,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "per_month": per_month,
        "total_active_hours": round(sum(v / 3600 for v in (active_map or {}).values()), 2),
        "total_commits": sum(g.commit_count for g in git_act) if git_act else 0,
    })
    write_json(q_dir / "quarter_trends.json", trends)
    if regimes:
        write_json(q_dir / "quarter_regimes.json", regimes)
    write_json(q_dir / "quarter_project_arcs.json", {
        repo: dict(months) for repo, months in project_arcs.items()
    })

    elapsed = round(time.monotonic() - t0, 2)
    write_json(q_dir / "manifest.json", {
        "scale": "quarter",
        "key": quarter_key,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "generated_at": datetime.now().isoformat(),
        "elapsed_s": elapsed,
        "files": sorted(p.name for p in q_dir.iterdir() if p.suffix == ".json"),
    })
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Half-year scaffold
# ══════════════════════════════════════════════════════════════════════════════

def generate_half(half_key: str, output: Path, *, force: bool = False,
                  all_features: list | None = None, batch: BatchSources | None = None) -> bool:
    period = parse_period("half", half_key)
    if period is None:
        return False

    h_dir = _half_dir(half_key, period, output)
    if (h_dir / "manifest.json").exists() and not force:
        return False
    t0 = time.monotonic()

    from ..sources.patterns import activity_trends

    s, e = period.start, period.end

    if batch and all_features:
        features = [f for f in all_features if s <= f.date <= e]
        git_act = [g for g in batch.git_daily if s <= g.date <= e]
        active_map = {d: v for d, v in batch.aw_active.items() if s <= d <= e} if isinstance(batch.aw_active, dict) else {}
    else:
        from ..sources.patterns import build_day_features
        from ..sources.git import daily_activity as git_daily
        from ..sources.activitywatch import active_seconds_by_date
        features = _safe(build_day_features, s, e, default=[])
        git_act = _safe(git_daily, start=s, end=e, default=[])
        active_map = _safe(active_seconds_by_date, s, e, default={})

    trends = _safe(activity_trends, features, default={}) if features else {}

    # Per-quarter summary
    q_keys = child_keys("half", half_key)
    per_quarter = []
    for qk in q_keys:
        qp = parse_period("quarter", qk)
        if not qp:
            continue
        q_active = sum(v / 3600 for d, v in (active_map or {}).items() if qp.start <= d <= qp.end)
        q_commits = sum(g.commit_count for g in (git_act or []) if qp.start <= g.date <= qp.end)
        per_quarter.append({
            "quarter": qk,
            "active_hours": round(q_active, 2),
            "commits": q_commits,
        })

    h_dir.mkdir(parents=True, exist_ok=True)

    write_json(h_dir / "half_metrics.json", {
        "half": half_key,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "per_quarter": per_quarter,
        "total_active_hours": round(sum(v / 3600 for v in (active_map or {}).values()), 2),
        "total_commits": sum(g.commit_count for g in git_act) if git_act else 0,
    })
    if trends:
        write_json(h_dir / "half_trends.json", trends)

    elapsed = round(time.monotonic() - t0, 2)
    write_json(h_dir / "manifest.json", {
        "scale": "half",
        "key": half_key,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "generated_at": datetime.now().isoformat(),
        "elapsed_s": elapsed,
        "files": sorted(p.name for p in h_dir.iterdir() if p.suffix == ".json"),
    })
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Year scaffold
# ══════════════════════════════════════════════════════════════════════════════

def generate_year(year_key: str, output: Path, *, force: bool = False,
                  all_features: list | None = None, batch: BatchSources | None = None) -> bool:
    period = parse_period("year", year_key)
    if period is None:
        return False

    y_dir = _year_dir(year_key, output)
    if (y_dir / "manifest.json").exists() and not force:
        return False

    t0 = time.monotonic()

    from ..sources.patterns import activity_trends

    s, e = period.start, min(period.end, date.today())

    if batch and all_features:
        features = [f for f in all_features if s <= f.date <= e]
        git_act = [g for g in batch.git_daily if s <= g.date <= e]
        active_map = {d: v for d, v in batch.aw_active.items() if s <= d <= e} if isinstance(batch.aw_active, dict) else {}
        chat_act = [c for c in _safe(lambda: batch.poly_summaries, default=[]) if hasattr(c, 'date') and s <= c.date <= e]
    else:
        from ..sources.patterns import build_day_features
        from ..sources.git import daily_activity as git_daily
        from ..sources.activitywatch import active_seconds_by_date
        from ..sources.polylogue import daily_activity as chat_daily
        features = _safe(build_day_features, s, e, default=[])
        git_act = _safe(git_daily, start=s, end=e, default=[])
        active_map = _safe(active_seconds_by_date, s, e, default={})
        chat_act = _safe(chat_daily, start=s, end=e, default=[])

    trends = _safe(activity_trends, features, default={}) if features else {}

    # Per-month metrics
    month_keys = period_keys_in_range("month", s, e)
    per_month = []
    for mk in month_keys:
        mp = parse_period("month", mk)
        if not mp:
            continue
        m_active = sum(v / 3600 for d, v in (active_map or {}).items() if mp.start <= d <= mp.end)
        m_commits = sum(g.commit_count for g in (git_act or []) if mp.start <= g.date <= mp.end)
        per_month.append({
            "month": mk,
            "active_hours": round(m_active, 2),
            "commits": m_commits,
        })

    # Per-project per-month arcs
    project_arcs: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for g in (git_act or []):
        mk = key_for_date("month", g.date)
        project_arcs[g.repo][mk] += g.commit_count

    # AI provider evolution
    provider_months: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for c in (chat_act or []):
        mk = key_for_date("month", c.date)
        provider_months[getattr(c, 'provider', 'unknown')][mk] += getattr(c, 'session_count', 0)

    y_dir.mkdir(parents=True, exist_ok=True)

    write_json(y_dir / "year_metrics.json", {
        "year": year_key,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "per_month": per_month,
        "total_active_hours": round(sum(v / 3600 for v in (active_map or {}).values()), 2),
        "total_commits": sum(g.commit_count for g in git_act) if git_act else 0,
    })
    write_json(y_dir / "year_project_arcs.json", {
        repo: dict(months) for repo, months in project_arcs.items()
    })
    if provider_months:
        write_json(y_dir / "year_ai_evolution.json", {
            prov: dict(months) for prov, months in provider_months.items()
        })
    if trends:
        write_json(y_dir / "year_trends.json", trends)

    elapsed = round(time.monotonic() - t0, 2)
    write_json(y_dir / "manifest.json", {
        "scale": "year",
        "key": year_key,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "generated_at": datetime.now().isoformat(),
        "elapsed_s": elapsed,
        "files": sorted(p.name for p in y_dir.iterdir() if p.suffix == ".json"),
    })
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Overview scaffold
# ══════════════════════════════════════════════════════════════════════════════

def generate_overview(output: Path, *, force: bool = False, data_start: date | None = None, data_end: date | None = None) -> bool:
    ov_dir = output / "overview"
    if (ov_dir / "manifest.json").exists() and not force:
        return False

    t0 = time.monotonic()

    from ..core.config import get_config
    from ..sources.patterns import build_day_features, activity_trends, work_regime_changes
    from ..sources.git import daily_activity as git_daily, repos
    from ..sources.activitywatch import active_seconds_by_date
    from ..sources.polylogue import daily_activity as chat_daily, iter_session_profiles
    from ..sources.sleep import entries as sleep_entries
    from ..sources.health import daily_health_summary, heart_rate_measurements
    from ..sources.substance import entries as substance_entries_fn, monthly_summary as substance_monthly
    from ..sources.activity_segments import segment_range, transition_bigrams
    from ..core.analytics import (
        detect_changepoints, detect_periodicity, detect_trend,
        detect_regimes, correlation_matrix as compute_corr_matrix,
        granger_test,
    )

    cfg = get_config()

    # Determine data range
    s = data_start or date(2020, 1, 1)
    e = data_end or date.today()

    # Source coverage
    sources_available = _safe(cfg.available_sources, default={})

    # Features for full dataset
    features = _safe(build_day_features, s, e, default=[])

    # Trends
    trends = _safe(activity_trends, features, default={}) if features else {}

    # Changepoints
    regime_changes = _safe(work_regime_changes, features, default=[]) if features else []

    # Periodicity
    periodicity = {}
    if features and len(features) > 14:
        for field_name in ("active_hours", "commit_count", "fragmentation", "sleep_hours"):
            vals = [float(v) for f in features if (v := getattr(f, field_name)) is not None]
            if len(vals) > 14:
                p = _safe(detect_periodicity, vals, default=[])
                if p:
                    periodicity[field_name] = p

    # HMM regime detection (uses hmmlearn if available, k-means fallback)
    regimes = None
    if features and len(features) >= 30:
        numeric_fields = [
            f.name for f in fields(features[0].__class__)
            if f.name not in ("date", "dominant_mode", "dominant_project")
        ]
        matrix = [[float(getattr(f, n) or 0) for n in numeric_fields] for f in features]
        regime_result = _safe(detect_regimes, matrix, feature_names=numeric_fields, default=None)
        if regime_result and regime_result.states:
            regimes = {
                "method": regime_result.method,
                "n_states": regime_result.n_states,
                "log_likelihood": regime_result.log_likelihood,
                "profiles": regime_result.profiles,
                "date_states": [
                    {"date": features[i].date.isoformat(), "state": regime_result.states[i]}
                    for i in range(min(len(features), len(regime_result.states)))
                ],
                "feature_names": numeric_fields,
            }

    # Git project arcs
    git_act = _safe(git_daily, start=s, end=e, default=[])
    project_arcs: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for g in (git_act or []):
        mk = key_for_date("month", g.date)
        project_arcs[g.repo][mk] += g.commit_count

    # AI evolution
    chat_act = _safe(chat_daily, start=s, end=e, default=[])
    provider_months: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for c in (chat_act or []):
        mk = key_for_date("month", c.date)
        provider_months[getattr(c, 'provider', 'unknown')][mk] += getattr(c, 'session_count', 0)

    # Sleep patterns
    all_sleep = _safe(sleep_entries, default=[])
    sleep_patterns = {}
    if all_sleep:
        sleep_by_month: dict[str, list] = defaultdict(list)
        for sl in all_sleep:
            d = getattr(sl, 'date', None) or getattr(sl, 'start', datetime.min).date() if hasattr(sl, 'start') else None
            if d:
                sleep_by_month[d.strftime("%Y-%m")].append(getattr(sl, 'total_minutes', 0) / 60)
        sleep_patterns = {
            mk: {"avg_hours": round(sum(vals) / len(vals), 2), "n_nights": len(vals)}
            for mk, vals in sorted(sleep_by_month.items()) if vals
        }

    # Substance summary (via proper source module)
    all_substance = _safe(substance_entries_fn, default=[])
    substance_summary = _safe(substance_monthly, start=s, end=e, default=[])

    # Correlation matrix (via proper core analytics)
    corr_matrix = {}
    if features and len(features) >= 10:
        numeric_names = [
            f.name for f in fields(features[0].__class__)
            if f.name not in ("date", "dominant_mode", "dominant_project")
        ]
        series = {name: [float(getattr(f, name) or 0) for f in features] for name in numeric_names}
        corr_matrix = _safe(compute_corr_matrix, series, default={})

    # Granger causality tests (sleep→productivity, etc.)
    granger_results = {}
    if features and len(features) >= 20:
        pairs = [
            ("sleep_hours", "active_hours"),
            ("sleep_hours", "fragmentation"),
            ("fragmentation", "commit_count"),
            ("active_hours", "commit_count"),
        ]
        for cause_name, effect_name in pairs:
            cause = [float(getattr(f, cause_name) or 0) for f in features]
            effect = [float(getattr(f, effect_name) or 0) for f in features]
            result = _safe(granger_test, cause, effect, max_lag=3, default=[])
            if result:
                granger_results[f"{cause_name}→{effect_name}"] = result

    # Health patterns
    health_all = _safe(daily_health_summary, start=s, end=e, default=[])
    health_patterns = {}
    if health_all:
        health_by_month: dict[str, list] = defaultdict(list)
        for h in health_all:
            mk = h.date.strftime("%Y-%m")
            health_by_month[mk].append(h)
        for mk, entries in sorted(health_by_month.items()):
            stress_vals = [h.stress_avg for h in entries if h.stress_avg is not None]
            hr_vals = [h.heart_rate_avg for h in entries if h.heart_rate_avg is not None]
            month_data = {"n_days": len(entries)}
            if stress_vals:
                month_data["avg_stress"] = round(sum(stress_vals) / len(stress_vals), 1)
            if hr_vals:
                month_data["avg_heart_rate"] = round(sum(hr_vals) / len(hr_vals), 1)
            health_patterns[mk] = month_data

    # Health × productivity correlations
    health_correlations = {}
    if health_all and features:
        feat_by_date = {f.date: f for f in features}
        stress_prod = []
        for h in health_all:
            f = feat_by_date.get(h.date)
            if f and h.stress_avg is not None:
                stress_prod.append((h.stress_avg, f.active_hours or 0, f.fragmentation or 0))
        if len(stress_prod) >= 10:
            health_correlations["stress_vs_active_hours"] = {
                "n": len(stress_prod),
                "avg_stress": round(sum(s for s, _, _ in stress_prod) / len(stress_prod), 1),
                "avg_active_hours": round(sum(a for _, a, _ in stress_prod) / len(stress_prod), 2),
            }

    # Global transition matrix (via proper source module)
    all_segs = _safe(segment_range, start=s, end=e, default=[])
    global_transitions = _safe(transition_bigrams, all_segs, default=None)

    # Source coverage matrix (which sources have data for which months)
    month_keys = period_keys_in_range("month", s, e)
    coverage = _build_source_coverage(features, git_act, chat_act, all_sleep, all_substance, month_keys, health=health_all)

    # ── Write ──
    ov_dir.mkdir(parents=True, exist_ok=True)

    write_json(ov_dir / "source_coverage.json", coverage)
    write_json(ov_dir / "trends.json", trends)
    write_json(ov_dir / "changepoints.json", regime_changes)
    if regimes:
        write_json(ov_dir / "regimes.json", regimes)
    write_json(ov_dir / "project_arcs.json", {
        repo: dict(months) for repo, months in project_arcs.items()
    })
    write_json(ov_dir / "ai_evolution.json", {
        prov: dict(months) for prov, months in provider_months.items()
    })
    write_json(ov_dir / "sleep_patterns.json", sleep_patterns)
    if health_patterns:
        write_json(ov_dir / "health_patterns.json", health_patterns)
    if health_correlations:
        write_json(ov_dir / "health_correlations.json", health_correlations)
    write_json(ov_dir / "substance_summary.json", substance_summary)
    if global_transitions:
        write_json(ov_dir / "transition_model.json", global_transitions)
    if periodicity:
        write_json(ov_dir / "periodicity.json", periodicity)
    if corr_matrix:
        write_json(ov_dir / "correlation_matrix.json", corr_matrix)
    if granger_results:
        write_json(ov_dir / "granger_causality.json", granger_results)

    # Substance × productivity cross-analysis
    if all_substance and features:
        sub_prod = _substance_productivity(all_substance, features)
        if sub_prod:
            write_json(ov_dir / "substance_productivity.json", sub_prod)

    # Copy generator source
    generator_src = Path(__file__)
    if generator_src.exists():
        shutil.copy2(generator_src, ov_dir / "generator.py")

    elapsed = round(time.monotonic() - t0, 2)
    write_json(ov_dir / "manifest.json", {
        "scale": "overview",
        "generated_at": datetime.now().isoformat(),
        "data_range": {"start": s.isoformat(), "end": e.isoformat()},
        "elapsed_s": elapsed,
        "sources_available": sources_available,
        "feature_count": len(features),
        "files": sorted(p.name for p in ov_dir.iterdir()),
    })

    return True


def _build_source_coverage(features, git_act, chat_act, sleep, substance, month_keys, health=None) -> dict:
    """Which sources have data for which months."""
    coverage: dict[str, dict[str, bool]] = {mk: {} for mk in month_keys}

    # AW (from features)
    for f in (features or []):
        mk = key_for_date("month", f.date)
        if mk in coverage:
            if f.active_hours is not None and f.active_hours > 0:
                coverage[mk]["activitywatch"] = True

    # Git
    for g in (git_act or []):
        mk = key_for_date("month", g.date)
        if mk in coverage:
            coverage[mk]["git"] = True

    # Chat
    for c in (chat_act or []):
        mk = key_for_date("month", c.date)
        if mk in coverage:
            coverage[mk]["polylogue"] = True

    # Sleep
    for sl in (sleep or []):
        d = getattr(sl, 'date', None)
        if d:
            mk = d.strftime("%Y-%m")
            if mk in coverage:
                coverage[mk]["sleep"] = True

    # Health
    for h in (health or []):
        mk = h.date.strftime("%Y-%m")
        if mk in coverage:
            coverage[mk]["health"] = True

    # Substance (SubstanceEntry dataclass)
    for entry in (substance or []):
        mk = entry.date.strftime("%Y-%m") if hasattr(entry, 'date') else str(entry.get("date", ""))[:7]
        if mk in coverage:
            coverage[mk]["substance"] = True

    return coverage


def _substance_productivity(substance_entries, features) -> dict:
    """Cross-analyze substance doses vs same-day productivity metrics.

    Accepts SubstanceEntry dataclasses from sources.substance module.
    """
    feature_by_date = {f.date: f for f in features}
    results = []
    for entry in substance_entries:
        d = entry.date
        f = feature_by_date.get(d)
        if not f:
            continue
        results.append({
            "date": d.isoformat(),
            "substance": entry.substance,
            "amount_mg": entry.amount_mg,
            "time": entry.time,
            "active_hours": round(f.active_hours, 2) if f.active_hours is not None else None,
            "fragmentation": round(f.fragmentation, 3) if f.fragmentation is not None else None,
            "commits": f.commit_count,
            "deep_work_min": round(f.deep_work_min, 1) if f.deep_work_min is not None else None,
        })

    if not results:
        return {}

    # Per-substance averages
    by_sub: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_sub[r["substance"]].append(r)

    summaries = {}
    for sub, rows in by_sub.items():
        n = len(rows)
        summaries[sub] = {
            "n_doses": n,
            "avg_active_hours": round(sum(r["active_hours"] for r in rows) / n, 2),
            "avg_fragmentation": round(sum(r["fragmentation"] for r in rows) / n, 3),
            "avg_commits": round(sum(r["commits"] for r in rows) / n, 1),
            "avg_deep_work_min": round(sum(r["deep_work_min"] for r in rows) / n, 1),
        }

    return {"entries": results, "per_substance": summaries}


# ══════════════════════════════════════════════════════════════════════════════
# Directory layout helpers
# ══════════════════════════════════════════════════════════════════════════════

def _day_dir(d: date, output: Path) -> Path:
    half = "H1" if d.month <= 6 else "H2"
    quarter = f"Q{(d.month - 1) // 3 + 1}"
    month_name = calendar.month_name[d.month]
    return output / str(d.year) / half / quarter / month_name / d.isoformat()


def _week_dir(week_key: str, period: Period, output: Path) -> Path:
    d = period.start
    half = "H1" if d.month <= 6 else "H2"
    quarter = f"Q{(d.month - 1) // 3 + 1}"
    month_name = calendar.month_name[d.month]
    return output / str(d.year) / half / quarter / month_name / week_key


def _month_dir(month_key: str, period: Period, output: Path) -> Path:
    d = period.start
    half = "H1" if d.month <= 6 else "H2"
    quarter = f"Q{(d.month - 1) // 3 + 1}"
    month_name = calendar.month_name[d.month]
    return output / str(d.year) / half / quarter / month_name


def _quarter_dir(quarter_key: str, period: Period, output: Path) -> Path:
    d = period.start
    half = "H1" if d.month <= 6 else "H2"
    quarter = f"Q{(d.month - 1) // 3 + 1}"
    return output / str(d.year) / half / quarter


def _half_dir(half_key: str, period: Period, output: Path) -> Path:
    half = "H1" if period.start.month <= 6 else "H2"
    return output / str(period.start.year) / half


def _year_dir(year_key: str, output: Path) -> Path:
    return output / year_key


# ══════════════════════════════════════════════════════════════════════════════
# Data range discovery
# ══════════════════════════════════════════════════════════════════════════════

def _discover_data_range() -> tuple[date, date]:
    """Find the earliest and latest dates with data across all sources."""
    today = date.today()
    earliest_dates: list[date] = []

    print("  Scanning sources for data range...")

    # Sleep (goes back to 2017)
    from ..sources.sleep import entries as sleep_entries
    sleep = _safe(sleep_entries, default=[])
    if sleep:
        dates = [getattr(s, 'date', None) for s in sleep if getattr(s, 'date', None)]
        if dates:
            earliest_dates.append(min(dates))
            print(f"    sleep:     {min(dates)} → {max(dates)} ({len(dates)} days)")

    # Substance (goes back to 2020)
    from ..sources.substance import entries as substance_entries
    sub = _safe(substance_entries, default=[])
    if sub:
        earliest_dates.append(sub[0].date)
        print(f"    substance: {sub[0].date} → {sub[-1].date} ({len(sub)} entries)")

    # Health (heart rate goes back to 2022-08)
    from ..sources.health import heart_rate_measurements
    hr = _safe(heart_rate_measurements, default=[])
    if hr:
        first_hr = hr[0].timestamp.date()
        last_hr = hr[-1].timestamp.date()
        earliest_dates.append(first_hr)
        print(f"    health:    {first_hr} → {last_hr} ({len(hr)} HR measurements)")

    # Git (goes back to 2021)
    from ..sources.git import daily_activity as git_daily
    for year in range(2013, today.year + 1):
        s = date(year, 1, 1)
        e = date(year, 12, 31) if year < today.year else today
        act = _safe(git_daily, start=s, end=e, default=[])
        if act:
            first = min(g.date for g in act)
            earliest_dates.append(first)
            print(f"    git:       {first} (first commit found)")
            break

    # AW (goes back to 2024)
    from ..sources.activitywatch import active_seconds_by_date
    for year in range(2020, today.year + 1):
        s = date(year, 1, 1)
        e = min(date(year, 12, 31), today)
        active = _safe(active_seconds_by_date, s, e, default={})
        if active:
            first = min(active.keys())
            earliest_dates.append(first)
            print(f"    aw:        {first} (first active day)")
            break

    # Reddit (goes back to 2013)
    try:
        from ..sources.reddit import iter_comments
        comments = list(iter_comments())
        if comments:
            first_dt = min(getattr(c, 'created', None) or getattr(c, 'date', None) for c in comments
                          if getattr(c, 'created', None) or getattr(c, 'date', None))
            first_d = first_dt.date() if hasattr(first_dt, 'date') else first_dt
            earliest_dates.append(first_d)
            print(f"    reddit:    {first_d} (first comment)")
    except Exception:
        pass

    data_start = min(earliest_dates) if earliest_dates else date(2024, 10, 14)
    print(f"  → Data range: {data_start} → {today}")

    return data_start, today


# ══════════════════════════════════════════════════════════════════════════════
# Orchestration: generate all ancestor levels for a date range
# ══════════════════════════════════════════════════════════════════════════════

def generate_hierarchy(start: date, end: date, output: Path, *, force: bool = False, dry_run: bool = False, skip_empty: bool = False):
    """Generate scaffolds for days in [start, end] and all their parent timescales."""

    # Collect all keys we need at each scale
    day_keys = period_keys_in_range("day", start, end)
    week_keys = period_keys_in_range("week", start, end)
    month_keys = period_keys_in_range("month", start, end)
    quarter_keys = period_keys_in_range("quarter", start, end)
    half_keys = period_keys_in_range("half", start, end)
    year_keys = period_keys_in_range("year", start, end)

    plan = [
        ("day", day_keys),
        ("week", week_keys),
        ("month", month_keys),
        ("quarter", quarter_keys),
        ("half", half_keys),
        ("year", year_keys),
    ]

    total_items = sum(len(keys) for _, keys in plan) + 1  # +1 for overview

    # ── Header ──
    print(f"\n{'─' * 60}")
    print(f"  📐 Narrative Scaffold Generator")
    print(f"{'─' * 60}")
    print(f"  Range:    {start} → {end}")
    print(f"  Output:   {output}")
    print(f"  Items:    {total_items} across 7 timescales")
    if force:
        print(f"  Mode:     force (overwrite existing)")
    print(f"{'─' * 60}")

    if skip_empty:
        print(f"  Skip-empty: ON (only days with data will be generated)")

    if dry_run:
        print()
        for scale, keys in plan:
            icon = Progress.SCALE_ICONS.get(scale, "▸")
            print(f"  {icon} {scale:10s} {len(keys):4d}  ({', '.join(keys[:5])}{'...' if len(keys) > 5 else ''})")
        print(f"  🌐 {'overview':10s}    1")
        print(f"\n  Total: {total_items} items")
        if skip_empty:
            print(f"  (actual count will be lower with --skip-empty)")
        return

    # Pre-compute DayFeatures for the full range ONCE.
    # This loads all sources (Spotify 258K, etc.) but only once.
    print(f"  Pre-computing DayFeatures for {start} → {end}...")
    t_feat = time.monotonic()
    all_features = _safe(_build_features_verbose, start, end, default=[])
    feat_elapsed = time.monotonic() - t_feat
    if all_features:
        non_zero = sum(1 for f in all_features if (f.active_hours or 0) > 0 or (f.commit_count or 0) > 0 or (f.sleep_hours or 0) > 0)
        print(f"  → {len(all_features)} days loaded, {non_zero} with activity ({feat_elapsed:.1f}s)\n")
    else:
        print(f"  → No features available ({feat_elapsed:.1f}s)\n")

    # Build set of days with any data (for --skip-empty)
    days_with_data: set[date] | None = None
    if skip_empty:
        print("  Scanning for days with data...")
        days_with_data = set()
        # Days with features that have non-zero values
        for f in (all_features or []):
            if (f.active_hours or 0) > 0 or (f.commit_count or 0) > 0 or (f.sleep_hours or 0) > 0 or (f.daily_steps or 0) > 0:
                days_with_data.add(f.date)
        print(f"    from features: {len(days_with_data)} days")
        # Days with substance
        from ..sources.substance import entries as _sub_entries
        sub_days = set()
        for entry in _safe(_sub_entries, default=[]):
            sub_days.add(entry.date)
        days_with_data.update(sub_days)
        print(f"    + substance: {len(sub_days)} days")
        # Days with health (heart rate)
        from ..sources.health import heart_rate_measurements as _hr_meas
        hr_days = set()
        for hr in _safe(_hr_meas, default=[]):
            hr_days.add(hr.timestamp.date())
        days_with_data.update(hr_days)
        print(f"    + heart rate: {len(hr_days)} days")
        # Days with sleep
        from ..sources.sleep import entries as _sleep_entries
        sleep_days = set()
        for sl in _safe(_sleep_entries, default=[]):
            if getattr(sl, 'date', None):
                sleep_days.add(sl.date)
        days_with_data.update(sleep_days)
        print(f"    + sleep: {len(sleep_days)} days")
        # Days with git
        from ..sources.git import daily_activity as _git_daily
        git_days = set()
        for g in _safe(_git_daily, start=start, end=end, default=[]):
            git_days.add(g.date)
        days_with_data.update(git_days)
        print(f"    + git: {len(git_days)} days")
        # Days with reddit
        from ..sources.reddit import daily_activity as _reddit_daily
        reddit_days = set()
        for r in _safe(_reddit_daily, start=start, end=end, default=[]):
            reddit_days.add(r.date)
        days_with_data.update(reddit_days)
        print(f"    + reddit: {len(reddit_days)} days")

        # Filter day_keys to only days with data
        original_count = len(day_keys)
        day_keys = [k for k in day_keys if date.fromisoformat(k) in days_with_data]
        plan[0] = ("day", day_keys)
        # Recalculate parent keys to only include periods that contain data days
        data_dates = sorted(days_with_data)
        if data_dates:
            week_keys = sorted(set(key_for_date("week", d) for d in data_dates if start <= d <= end))
            month_keys = sorted(set(key_for_date("month", d) for d in data_dates if start <= d <= end))
            quarter_keys = sorted(set(key_for_date("quarter", d) for d in data_dates if start <= d <= end))
            half_keys = sorted(set(key_for_date("half", d) for d in data_dates if start <= d <= end))
            year_keys = sorted(set(key_for_date("year", d) for d in data_dates if start <= d <= end))
            plan = [
                ("day", day_keys),
                ("week", week_keys),
                ("month", month_keys),
                ("quarter", quarter_keys),
                ("half", half_keys),
                ("year", year_keys),
            ]
        skipped_empty = original_count - len(day_keys)
        total_items = sum(len(keys) for _, keys in plan) + 1
        print(f"  → {len(days_with_data)} days with data, {skipped_empty} empty days skipped")
        print(f"  → {total_items} items to generate\n")

    # Batch-load all sources once for the day generator
    print("  Batch-loading sources for day generation...")
    t_batch = time.monotonic()
    batch = _safe(BatchSources, start, end, default=None)
    if batch:
        print(f"  → Batch load complete ({time.monotonic() - t_batch:.1f}s)\n")
    else:
        print(f"  → Batch load failed, falling back to per-day queries\n")

    generators = {
        "day": lambda k: generate_day(date.fromisoformat(k), output, force=force, all_features=all_features, batch=batch),
        "week": lambda k: generate_week(k, output, force=force, all_features=all_features, batch=batch),
        "month": lambda k: generate_month(k, output, force=force, all_features=all_features, batch=batch),
        "quarter": lambda k: generate_quarter(k, output, force=force, all_features=all_features, batch=batch),
        "half": lambda k: generate_half(k, output, force=force, all_features=all_features, batch=batch),
        "year": lambda k: generate_year(k, output, force=force, all_features=all_features, batch=batch),
    }

    grand_t0 = time.monotonic()
    grand_generated = 0
    grand_skipped = 0
    grand_failed = 0
    scale_summaries: list[str] = []

    for scale, keys in plan:
        if not keys:
            continue
        prog = Progress(scale, len(keys))
        gen = generators[scale]
        for key in keys:
            prog.start_item(key)
            try:
                if gen(key):
                    prog.finish_item(key, status="ok")
                else:
                    prog.finish_item(key, status="skip")
            except Exception as exc:
                prog.finish_item(key, status=f"error: {exc}")
                traceback.print_exc(file=sys.stderr)
        summary = prog.summary()
        if summary:
            scale_summaries.append(f"  {Progress.SCALE_ICONS.get(scale, '▸')} {scale:10s} {summary}")
        grand_generated += prog.generated
        grand_skipped += prog.skipped
        grand_failed += prog.failed

    # Overview
    prog = Progress("overview", 1)
    prog.start_item("overview")
    try:
        if generate_overview(output, force=force, data_start=start, data_end=end):
            prog.finish_item("overview", status="ok")
            grand_generated += 1
        else:
            prog.finish_item("overview", status="skip")
            grand_skipped += 1
    except Exception as exc:
        prog.finish_item("overview", status=f"error: {exc}")
        grand_failed += 1
    summary = prog.summary()
    if summary:
        scale_summaries.append(f"  🌐 {'overview':10s} {summary}")

    # ── Final report ──
    grand_elapsed = time.monotonic() - grand_t0
    print(f"\n{'═' * 60}")
    print(f"  ✓ Scaffold Complete")
    print(f"{'═' * 60}")
    for line in scale_summaries:
        print(line)
    print(f"{'─' * 60}")
    parts = []
    if grand_generated:
        parts.append(f"{grand_generated} generated")
    if grand_skipped:
        parts.append(f"{grand_skipped} skipped")
    if grand_failed:
        parts.append(f"{grand_failed} failed")
    if grand_elapsed < 60:
        time_str = f"{grand_elapsed:.1f}s"
    else:
        time_str = f"{grand_elapsed / 60:.1f}m"
    print(f"  Total: {' · '.join(parts)} in {time_str}")
    print(f"  Output: {output}")
    print(f"{'═' * 60}\n")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generate narrative scaffold from lynchpin sources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output", "-o", type=Path,
                        default=Path("artefacts/retrospective/scaffold"),
                        help="Output directory (default: artefacts/retrospective/scaffold/)")
    parser.add_argument("--day", type=str, help="Generate scaffold for a single day (YYYY-MM-DD)")
    parser.add_argument("--start", type=str, help="Start date for range generation")
    parser.add_argument("--end", type=str, help="End date for range generation")
    parser.add_argument("--overview-only", action="store_true", help="Only generate overview")
    parser.add_argument("--force", action="store_true", help="Overwrite existing folders")
    parser.add_argument("--skip-empty", action="store_true", help="Skip days with no data from any source")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be generated")

    args = parser.parse_args()
    output = args.output.resolve()

    if args.overview_only:
        generate_overview(output, force=args.force)
        return

    if args.day:
        d = date.fromisoformat(args.day)
        if args.dry_run:
            print(f"Would generate: day {d} + parent levels")
            return
        generate_hierarchy(d, d, output, force=args.force, skip_empty=args.skip_empty)
        return

    if args.start and args.end:
        s = date.fromisoformat(args.start)
        e = date.fromisoformat(args.end)
        generate_hierarchy(s, e, output, force=args.force, dry_run=args.dry_run, skip_empty=args.skip_empty)
        return

    if args.start or args.end:
        print("Both --start and --end are required for range generation", file=sys.stderr)
        sys.exit(1)

    # Full dataset — default to skip-empty for auto-discovered ranges
    print("Discovering data range...")
    data_start, data_end = _discover_data_range()
    print(f"Data range: {data_start} → {data_end}")
    generate_hierarchy(data_start, data_end, output, force=args.force, dry_run=args.dry_run,
                       skip_empty=args.skip_empty or not (args.start and args.end))


if __name__ == "__main__":
    main()
