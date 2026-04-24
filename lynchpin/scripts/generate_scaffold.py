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
import math
import re
import sqlite3
import shutil
import sys
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
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

_BULK_HISTORY_DAYS = 120


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


@dataclass(frozen=True)
class DateSpan:
    """Inclusive date coverage for one source."""

    start: date
    end: date
    count: int = 0


@dataclass
class AWDerivedBatch:
    focus_spans: list
    app_sessions: list
    deep_work: list
    sustained_focus: list
    fragmentation: list
    attention: list
    circadian: list


_AW_DERIVED_CACHE: dict[tuple[date, date], AWDerivedBatch] = {}


def _span_from_dates(values: list[date]) -> DateSpan | None:
    dates = [d for d in values if d is not None]
    if not dates:
        return None
    return DateSpan(min(dates), max(dates), len(dates))


def _clip_dates(start: date, end: date, span: DateSpan | None, *, pad_start_days: int = 0) -> tuple[date, date] | None:
    """Intersect a requested inclusive date range with known source coverage."""
    requested_start = start - timedelta(days=pad_start_days)
    if span is None:
        return (requested_start, end)
    clipped_start = max(requested_start, span.start)
    clipped_end = min(end, span.end)
    if clipped_start > clipped_end:
        return None
    return clipped_start, clipped_end


def _span_text(span: DateSpan | None) -> str:
    if span is None:
        return "unknown"
    suffix = f", {span.count}" if span.count else ""
    return f"{span.start} → {span.end}{suffix}"


def _union_span(*spans: DateSpan | None) -> DateSpan | None:
    present = [s for s in spans if s is not None]
    if not present:
        return None
    return DateSpan(
        min(s.start for s in present),
        max(s.end for s in present),
        sum(s.count for s in present),
    )


def _date_chunks(start: date, end: date, *, days: int = 31):
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=days - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _should_chunk(key: str, start: date, end: date) -> bool:
    if (end - start).days < 45:
        return False
    return key in {"aw", "timeline"}


def _is_bulk_history(start: date, end: date) -> bool:
    return (end - start).days > _BULK_HISTORY_DAYS


def _sqlite_timestamp_span(db: Path, query: str, *, params: tuple = ()) -> DateSpan | None:
    if not db.exists():
        return None
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute(query, params).fetchone()
    if not row or row[0] is None or row[1] is None:
        return None
    first = int(row[0])
    last = int(row[1])
    # ActivityWatch stores ns, Atuin may store ns/ms/s.
    divisor = 1
    if max(abs(first), abs(last)) > 10**14:
        divisor = 1_000_000_000
    elif max(abs(first), abs(last)) > 10**11:
        divisor = 1_000
    return DateSpan(
        datetime.fromtimestamp(first / divisor, tz=timezone.utc).date(),
        datetime.fromtimestamp(last / divisor, tz=timezone.utc).date(),
    )


def _coverage_dates(coverage: dict[str, DateSpan], key: str, start: date, end: date, *, pad_start_days: int = 0) -> tuple[date, date] | None:
    return _clip_dates(start, end, coverage.get(key), pad_start_days=pad_start_days)


def _load_date_range(label: str, fn, key: str, coverage: dict[str, DateSpan] | None,
                     start: date, end: date, *, default=None, positional: bool = False,
                     pad_start_days: int = 0):
    """Load one source only over its known coverage window."""
    default = [] if default is None else default
    if coverage is not None and key not in coverage:
        print(f"      {label}... (skipped; no available coverage)")
        return default
    span = (coverage or {}).get(key)
    clipped = _clip_dates(start, end, span, pad_start_days=pad_start_days)
    print(f"      {label}...", end=" ", flush=True)
    if clipped is None:
        print(f"(skipped; coverage {_span_text(span)})")
        return default
    t = time.monotonic()
    if _should_chunk(key, clipped[0], clipped[1]) and not positional:
        result = []
        for chunk_start, chunk_end in _date_chunks(clipped[0], clipped[1]):
            chunk = _safe(fn, start=chunk_start, end=chunk_end, default=[])
            result.extend(chunk)
    elif positional:
        result = _safe(fn, clipped[0], clipped[1], default=default)
    else:
        result = _safe(fn, start=clipped[0], end=clipped[1], default=default)
    elapsed = time.monotonic() - t
    count = len(result) if isinstance(result, (list, dict, tuple)) else 0
    clipped_note = "" if clipped == (start, end) else f", clipped {clipped[0]} → {clipped[1]}"
    print(f"({count} records, {elapsed:.1f}s{clipped_note})")
    return result


def _load_datetime_range(label: str, fn, key: str, coverage: dict[str, DateSpan] | None,
                         start: date, end: date, *, default=None, pad_start_days: int = 0):
    if coverage is not None and key not in coverage:
        print(f"      {label}... (skipped; no available coverage)")
        return [] if default is None else default
    clipped = _coverage_dates(coverage or {}, key, start, end, pad_start_days=pad_start_days)
    default = [] if default is None else default
    print(f"      {label}...", end=" ", flush=True)
    if clipped is None:
        print(f"(skipped; coverage {_span_text((coverage or {}).get(key))})")
        return default
    s_dt, e_dt = date_to_dt_range(clipped[0], clipped[1])
    t = time.monotonic()
    if _should_chunk(key, clipped[0], clipped[1]):
        result = []
        for chunk_start, chunk_end in _date_chunks(clipped[0], clipped[1]):
            s_dt, e_dt = date_to_dt_range(chunk_start, chunk_end)
            chunk = _safe(fn, start=s_dt, end=e_dt, default=[])
            result.extend(chunk)
    else:
        result = _safe(fn, start=s_dt, end=e_dt, default=default)
    elapsed = time.monotonic() - t
    count = len(result) if isinstance(result, (list, dict, tuple)) else 0
    clipped_note = "" if clipped == (start, end) else f", clipped {clipped[0]} → {clipped[1]}"
    print(f"({count} records, {elapsed:.1f}s{clipped_note})")
    return result


def _attention_from_spans(spans: list) -> list:
    from ..sources.activitywatch import AttentionMetrics

    by_day: dict[date, dict[str, float]] = {}
    for span in spans:
        if span.duration_s < 60 or span.kind != "focused" or not span.project:
            continue
        bucket = by_day.setdefault(span.date, {})
        bucket[span.project] = bucket.get(span.project, 0) + span.duration_s

    result = []
    for day, projects in sorted(by_day.items()):
        total = sum(projects.values())
        if total <= 0:
            continue
        probs = [v / total for v in projects.values()]
        entropy = -sum(p * math.log2(p) for p in probs if p > 0)
        sorted_vals = sorted(projects.values())
        n = len(sorted_vals)
        gini = (
            2 * sum((i + 1) * v for i, v in enumerate(sorted_vals)) / (n * total)
            - (n + 1) / n
        ) if n > 0 else 0
        top = max(projects, key=projects.get)
        result.append(AttentionMetrics(
            date=day,
            entropy=round(entropy, 3),
            gini=round(gini, 3),
            top_project=top,
            project_count=n,
        ))
    return result


def _circadian_from_spans(spans: list) -> list:
    from ..core.primitives import TopN, duration_s, split_by_hour
    from ..sources.activitywatch import CircadianProfile

    buckets: dict[tuple[date, int], tuple[TopN, TopN, float, float]] = {}
    for span in spans:
        if span.duration_s < 30:
            continue
        for hour, seg in split_by_hour(span.start, span.end):
            key = (span.date, hour)
            modes, projects, active, recovery = buckets.get(key, (TopN(1), TopN(1), 0.0, 0.0))
            mins = duration_s(seg) / 60
            if span.kind == "afk":
                recovery += mins
            else:
                active += mins
                if span.mode:
                    modes.add(span.mode, mins)
                if span.project:
                    projects.add(span.project, mins)
            buckets[key] = (modes, projects, active, recovery)

    result = []
    for (day, hour), (modes, projects, active, recovery) in sorted(buckets.items()):
        if active > 0 or recovery > 0:
            result.append(CircadianProfile(
                day,
                hour,
                round(active, 1),
                round(recovery, 1),
                modes.dominant,
                projects.dominant,
            ))
    return result


def _load_aw_derived(start: date, end: date, coverage: dict[str, DateSpan] | None,
                     active_map: dict[date, float] | None) -> AWDerivedBatch:
    """Load all ActivityWatch derived scaffold surfaces without dropping layers.

    The public AW functions are expensive over long ranges because attention and
    circadian recompute focus spans with different duration thresholds. This path
    computes focus spans once per active day, reuses the source cache for session
    and focus-block APIs, and derives attention/circadian from the same spans.
    """
    empty = AWDerivedBatch([], [], [], [], [], [], [])
    span = (coverage or {}).get("aw")
    clipped = _clip_dates(start, end, span)
    print("      AW derived surfaces...", end=" ", flush=True)
    if clipped is None:
        print(f"(skipped; coverage {_span_text(span)})")
        return empty
    if not isinstance(active_map, dict):
        print("(skipped; no active-day map)")
        return empty

    active_days = [
        d for d, seconds in sorted(active_map.items())
        if clipped[0] <= d <= clipped[1] and seconds > 0
    ]
    if not active_days:
        print("(0 records, no active days)")
        return empty

    key = (clipped[0], clipped[1])
    if key in _AW_DERIVED_CACHE:
        cached = _AW_DERIVED_CACHE[key]
        total = (
            len(cached.focus_spans) + len(cached.app_sessions) + len(cached.deep_work)
            + len(cached.sustained_focus) + len(cached.fragmentation)
            + len(cached.attention) + len(cached.circadian)
        )
        print(f"({total} records, cached)")
        return cached

    from ..sources.activitywatch import (
        focus_spans, app_sessions, deep_work, sustained_focus, fragmentation,
    )

    t = time.monotonic()
    batch = AWDerivedBatch([], [], [], [], [], [], [])
    failures = 0
    total_days = len(active_days)
    print(f"(computing {total_days} active days)", flush=True)
    for i, day in enumerate(active_days, 1):
        s_dt = datetime.combine(day, datetime.min.time())
        e_dt = datetime.combine(day + timedelta(days=1), datetime.min.time())
        try:
            spans = focus_spans(start=s_dt, end=e_dt)
            batch.focus_spans.extend(spans)
            batch.app_sessions.extend(app_sessions(start=s_dt, end=e_dt))
            batch.deep_work.extend(deep_work(start=s_dt, end=e_dt))
            batch.sustained_focus.extend(sustained_focus(start=s_dt, end=e_dt))
            batch.fragmentation.extend(fragmentation(start=day, end=day))
            batch.attention.extend(_attention_from_spans(spans))
            batch.circadian.extend(_circadian_from_spans(spans))
        except Exception as exc:
            failures += 1
            print(f"        warning: AW derived failed for {day}: {exc}", file=sys.stderr)
        if i == total_days or i % 25 == 0:
            elapsed = time.monotonic() - t
            print(f"        AW derived {i}/{total_days} days ({elapsed:.1f}s)", flush=True)

    _AW_DERIVED_CACHE[key] = batch
    total_records = (
        len(batch.focus_spans) + len(batch.app_sessions) + len(batch.deep_work)
        + len(batch.sustained_focus) + len(batch.fragmentation)
        + len(batch.attention) + len(batch.circadian)
    )
    elapsed = time.monotonic() - t
    fail_note = f", {failures} failed" if failures else ""
    print(f"      AW derived complete ({total_records} records, {elapsed:.1f}s{fail_note})")
    return batch


# Substance access via proper source module
# See lynchpin/sources/substance.py for full API


def _build_features_verbose(start: date, end: date, coverage: dict[str, DateSpan] | None = None):
    """Wrapper around build_day_features that prints per-source progress."""
    from ..sources.patterns import _safe_fetch, DayFeatures
    from ..core.parse import iter_dates

    def _load(label, fn, *args, **kwargs):
        print(f"      {label}...", end=" ", flush=True)
        t = time.monotonic()
        result = _safe(fn, *args, default=kwargs.pop('default', []), **kwargs)
        elapsed = time.monotonic() - t
        count = len(result) if isinstance(result, (list, dict)) else 0
        print(f"({count} records, {elapsed:.1f}s)")
        return result

    from ..sources.activitywatch import active_seconds_by_date
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

    aw_active = _load_date_range("AW active", active_seconds_by_date, "aw", coverage, start, end, default={}, positional=True)
    aw_derived = _load_aw_derived(start, end, coverage, aw_active if isinstance(aw_active, dict) else None)
    dw_blocks = aw_derived.deep_work
    sf_blocks = aw_derived.sustained_focus
    frag_list = aw_derived.fragmentation
    att_list = aw_derived.attention
    sessions = aw_derived.app_sessions
    git_act = _load_date_range("Git", daily_activity, "git", coverage, start, end)
    shells = _load_datetime_range("Terminal", shell_sessions, "terminal", coverage, start, end)
    chat_act = _load_date_range("Polylogue", chat_daily, "polylogue", coverage, start, end)
    sleep_data_raw = _load("Sleep", sleep_entries, default={})
    spotify_act = _load_date_range("Spotify", daily_listening, "spotify", coverage, start, end)
    reddit_act = _load_date_range("Reddit", reddit_daily, "reddit", coverage, start, end)
    steps_data = _load_date_range("Health steps", daily_steps, "health_steps", coverage, start, end)
    vitality_data = _load_date_range("Health vitality", daily_vitality, "health", coverage, start, end)
    health_sum = _load_date_range("Health summary", daily_health_summary, "health", coverage, start, end)
    web_act = _load_date_range("Web browsing", daily_browsing, "web", coverage, start, end)
    msg_act = _load_date_range("Messenger", daily_messenger_activity, "messenger", coverage, start, end)
    bm_act = _load_date_range("Raindrop", daily_raindrop_activity, "raindrop", coverage, start, end)
    sub_act = _load_date_range("Substance", substance_daily, "substance", coverage, start, end)

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


def _capture_days_with_data(
    coverage: dict[str, DateSpan],
    start: date,
    end: date,
) -> dict[str, set[date]]:
    """Return capture source days that should not be skipped."""
    capture_days: dict[str, set[date]] = {
        "clipboard": set(),
        "irc": set(),
        "raw_log": set(),
    }

    from ..sources.clipboard import entries_in_range as _clipboard_entries
    from ..sources.irc import conversations_in_range as _irc_conversations
    from ..sources.raw_log import entries_in_range as _raw_log_entries

    clipboard_window = _coverage_dates(coverage, "clipboard", start, end)
    if clipboard_window:
        for entry in _safe(_clipboard_entries, start=clipboard_window[0], end=clipboard_window[1], default=[]):
            if getattr(entry, "date", None):
                capture_days["clipboard"].add(entry.date)

    irc_window = _coverage_dates(coverage, "irc", start, end)
    if irc_window:
        for conv in _safe(_irc_conversations, start=irc_window[0], end=irc_window[1], default=[]):
            if getattr(conv, "start", None):
                capture_days["irc"].add(conv.start.date())

    raw_log_window = _coverage_dates(coverage, "raw_log", start, end)
    if raw_log_window:
        for entry in _safe(_raw_log_entries, start=raw_log_window[0], end=raw_log_window[1], default=[]):
            if getattr(entry, "date", None):
                capture_days["raw_log"].add(entry.date)

    return capture_days


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rounded_avg(values: list[float], digits: int = 1) -> float | None:
    avg = _avg(values)
    return round(avg, digits) if avg is not None else None


def _summarize_health(rows: list) -> dict[str, Any]:
    """Compact health/recovery rollup for narrative period scaffolds."""
    if not rows:
        return {}

    def vals(name: str) -> list[float]:
        return [float(v) for row in rows if (v := getattr(row, name, None)) is not None]

    summary: dict[str, Any] = {"n_days": len(rows)}
    field_specs = {
        "steps": ("avg_steps", 0),
        "stress_avg": ("avg_stress", 1),
        "heart_rate_avg": ("avg_heart_rate", 1),
        "heart_rate_resting": ("avg_resting_heart_rate", 1),
        "hrv_rmssd_avg": ("avg_hrv_rmssd", 2),
        "spo2_avg": ("avg_spo2", 1),
        "respiratory_avg": ("avg_respiratory_rate", 1),
        "floors": ("avg_floors", 1),
        "skin_temp_avg": ("avg_skin_temp", 2),
        "vitality_score": ("avg_vitality", 1),
        "calories": ("avg_daily_calories", 0),
    }
    for source_name, (target_name, digits) in field_specs.items():
        value = _rounded_avg(vals(source_name), digits)
        if value is not None:
            summary[target_name] = value

    snoring_seconds = sum(int(getattr(row, "snoring_duration_s", 0) or 0) for row in rows)
    if snoring_seconds:
        summary["total_snoring_min"] = round(snoring_seconds / 60, 1)

    populated = Counter()
    for row in rows:
        for source_name in field_specs:
            if getattr(row, source_name, None) is not None:
                populated[source_name] += 1
    if populated:
        summary["days_with_signal"] = dict(populated)
    return summary


def _summarize_sleep(records: list, architecture: list | None = None) -> dict[str, Any]:
    """Compact sleep rollup from inferred/entry records plus optional architecture."""
    if not records and not architecture:
        return {}

    summary: dict[str, Any] = {}
    if records:
        bed_hours = [
            float(getattr(record, "bed_duration_min", 0) or 0) / 60
            for record in records
            if getattr(record, "bed_duration_min", None)
        ]
        sleep_hours = [
            float(getattr(record, "sleep_duration_min", getattr(record, "total_minutes", 0)) or 0) / 60
            for record in records
            if getattr(record, "sleep_duration_min", getattr(record, "total_minutes", None)) is not None
        ]
        scores = [
            float(v)
            for record in records
            if (v := getattr(record, "sleep_score", getattr(record, "avg_score", None))) is not None
        ]
        if bed_hours:
            summary["avg_bed_hours"] = round(sum(bed_hours) / len(bed_hours), 2)
        if sleep_hours:
            summary["avg_sleep_hours"] = round(sum(sleep_hours) / len(sleep_hours), 2)
        if scores:
            summary["avg_sleep_score"] = round(sum(scores) / len(scores), 1)
        summary["n_sleep_records"] = len(records)
        summary["sources"] = dict(Counter(getattr(record, "source", "watch") for record in records))
        confidences = [
            float(v)
            for record in records
            if (v := getattr(record, "confidence", None)) is not None
        ]
        if confidences:
            summary["avg_confidence"] = round(sum(confidences) / len(confidences), 2)
            summary["low_confidence_records"] = sum(1 for v in confidences if v < 0.5)
        evidence = Counter()
        for record in records:
            evidence.update(getattr(record, "evidence", ()) or ())
        if evidence:
            summary["evidence"] = dict(evidence.most_common())
        keypresses = sum(int(getattr(record, "keypress_count", 0) or 0) for record in records)
        if keypresses:
            summary["sleep_window_keypresses"] = keypresses
        aw_overlap = [
            float(v)
            for record in records
            if (v := getattr(record, "aw_active_overlap_pct", None)) is not None and float(v) > 0
        ]
        if aw_overlap:
            summary["avg_aw_active_overlap_pct"] = round(sum(aw_overlap) / len(aw_overlap), 1)

    if architecture:
        summary["n_architecture_records"] = len(architecture)
        for attr, key in (
            ("awake_min", "avg_awake_min"),
            ("light_min", "avg_light_min"),
            ("deep_min", "avg_deep_min"),
            ("rem_min", "avg_rem_min"),
            ("stage_transitions", "avg_stage_transitions"),
        ):
            values = [float(getattr(row, attr)) for row in architecture if getattr(row, attr, None) is not None]
            value = _rounded_avg(values, 1)
            if value is not None:
                summary[key] = value
    return summary


def _polylogue_cost_payload(sessions: list | None) -> dict[str, Any]:
    if not sessions:
        return {"status": "absent", "display_total_usd": None}

    measured = [session for session in sessions if not bool(getattr(session, "cost_is_estimated", False))]
    estimated = [session for session in sessions if bool(getattr(session, "cost_is_estimated", False))]
    measured_total = round(sum(float(getattr(session, "total_cost_usd", 0) or 0) for session in measured), 4)
    estimated_total = round(sum(float(getattr(session, "total_cost_usd", 0) or 0) for session in estimated), 4)

    if measured and estimated:
        status = "partial"
        display_total = measured_total
    elif measured:
        status = "measured"
        display_total = measured_total
    elif estimated_total > 0:
        status = "estimated"
        display_total = None
    else:
        status = "estimated_zero"
        display_total = None

    return {
        "status": status,
        "display_total_usd": display_total,
        "measured_total_usd": measured_total if measured else None,
        "estimated_total_usd": estimated_total if estimated else None,
        "session_counts": {
            "measured": len(measured),
            "estimated": len(estimated),
        },
    }


def _summarize_ai(
    days: list,
    events: list | None = None,
    *,
    sessions: list | None = None,
    transcripts: list | None = None,
) -> dict[str, Any]:
    """Compact Polylogue rollup for narrative period scaffolds."""
    if not days and not events and not sessions and not transcripts:
        return {}
    providers: Counter = Counter()
    repos: Counter = Counter()
    kinds: Counter = Counter()
    event_repo_presence: set[str] = set()
    total_sessions = total_messages = total_words = 0
    total_cost = 0.0
    if sessions:
        for session in sessions:
            total_sessions += 1
            total_messages += int(getattr(session, "message_count", 0) or 0)
            total_words += int(getattr(session, "word_count", 0) or 0)
            total_cost += float(getattr(session, "total_cost_usd", 0.0) or 0.0)
            providers[str(getattr(session, "provider", "unknown") or "unknown")] += 1
            for project in getattr(session, "work_event_projects", ()) or ():
                repos[str(project)] += 1
            if kind := getattr(session, "work_event_kind", None):
                kinds[str(kind)] += 1
    else:
        for day in days or []:
            total_sessions += getattr(day, "session_count", 0) or 0
            total_messages += getattr(day, "total_messages", 0) or 0
            total_words += getattr(day, "total_words", 0) or 0
            total_cost += getattr(day, "total_cost_usd", 0.0) or 0.0
            providers.update(getattr(day, "providers", {}) or {})
            repos.update(getattr(day, "repos_active", ()) or ())
            kinds.update(getattr(day, "work_event_breakdown", {}) or {})
    for event in events or []:
        if kind := getattr(event, "kind", None):
            kinds[kind] += 1
        for path in getattr(event, "file_paths", ()) or ():
            path_obj = Path(path)
            repo = path_obj.parts[3] if str(path).startswith("/realm/project/") and len(path_obj.parts) > 3 else None
            if repo:
                event_repo_presence.add(repo)

    role_counts: Counter = Counter()
    kind_counts: Counter = Counter()
    token_estimates = {
        "user_prompts": 0,
        "dialogue": 0,
        "all_messages": 0,
    }
    for transcript in transcripts or []:
        token_estimates["user_prompts"] += int(getattr(transcript, "user_prompt_tokens", 0) or 0)
        token_estimates["dialogue"] += int(getattr(transcript, "dialogue_tokens", 0) or 0)
        token_estimates["all_messages"] += int(getattr(transcript, "all_message_tokens", 0) or 0)
        for message in getattr(transcript, "messages", ()) or ():
            role_counts[str(getattr(message, "role", "unknown") or "unknown")] += 1
            kind_counts[str(getattr(message, "kind", getattr(message, "role", "unknown")) or "unknown")] += 1

    summary = {
        "session_count": total_sessions,
        "total_messages": total_messages,
        "total_words": total_words,
        "providers": dict(providers.most_common()),
        "repos_active": dict(repos.most_common(20)),
        "work_event_breakdown": dict(kinds.most_common()),
    }
    summary["cost"] = _polylogue_cost_payload(sessions)
    if total_cost > 0 and summary["cost"]["status"] in {"measured", "partial"}:
        summary["total_cost_usd"] = round(total_cost, 4)
    if any(token_estimates.values()):
        summary["token_estimates"] = token_estimates
    if role_counts:
        summary["message_roles"] = dict(role_counts.most_common())
    if kind_counts:
        summary["message_kinds"] = dict(kind_counts.most_common())
    for repo in sorted(event_repo_presence):
        repos[repo] = max(int(repos.get(repo, 0) or 0), 1)
    summary["repos_active"] = dict(repos.most_common(20))
    return summary


def _relative_seconds(origin: datetime, value: datetime) -> int | float:
    return _rounded_number(max((value - origin).total_seconds(), 0.0), 3) or 0


def _build_focus_timeline_payload(d: date, rows: list[Any]) -> dict[str, Any]:
    from ..core.title_features import extract_title_features

    tzinfo = next((row.start.tzinfo for row in rows if getattr(row.start, "tzinfo", None)), None)
    origin = datetime.combine(d, datetime.min.time(), tzinfo=tzinfo)
    titles: list[dict[str, Any]] = []
    title_ids: dict[tuple[str, str, str | None, str | None], int] = {}
    encoded_rows: list[dict[str, Any]] = []
    kinds: Counter = Counter()
    sources: Counter = Counter()
    apps: Counter = Counter()
    total_keypresses = 0
    covered_gap_s = 0.0
    healed_gap_s = 0.0
    raw_row_count = len(rows)
    current_group: dict[str, Any] | None = None
    current_group_start: datetime | None = None
    current_group_end: datetime | None = None
    current_subspans: list[list[Any]] = []

    def flush_group() -> None:
        nonlocal current_group, current_group_start, current_group_end, current_subspans
        if current_group is None:
            return
        if current_subspans:
            if len(current_subspans) == 1 and current_subspans[0][0] == 0:
                sub = current_subspans[0]
                current_group["title_id"] = sub[2]
            else:
                current_group["subspans"] = current_subspans
        encoded_rows.append(current_group)
        current_group = None
        current_group_start = None
        current_group_end = None
        current_subspans = []

    for row in rows:
        kinds[str(row.kind)] += 1
        sources[str(getattr(row, "source", "unknown") or "unknown")] += 1
        if getattr(row, "app", None):
            apps[str(row.app)] += 1
        total_keypresses += int(getattr(row, "keypress_count", 0) or 0)
        if getattr(row, "kind", None) == "coverage_gap":
            covered_gap_s += float(getattr(row, "duration_s", 0) or 0)
        if getattr(row, "source", None) == "afk_gap_healed":
            healed_gap_s += float(getattr(row, "duration_s", 0) or 0)

        title_id = None
        if getattr(row, "app", None) and getattr(row, "title", None):
            key = (str(row.app), str(row.title), getattr(row, "mode", None), getattr(row, "project", None))
            title_id = title_ids.get(key)
            if title_id is None:
                feat = extract_title_features(str(row.app), str(row.title))
                parsed = {
                    "app_kind": feat.app_kind,
                    "tool": feat.tool,
                    "project": feat.project,
                    "domain": feat.domain,
                    "domain_category": feat.domain_category,
                    "is_ai_tool": feat.is_ai_tool,
                    "is_ai_active": feat.is_ai_active,
                }
                parsed = {k: v for k, v in parsed.items() if v not in (None, False, "")}
                title_id = len(titles)
                title_ids[key] = title_id
                titles.append({
                    "id": title_id,
                    "app": row.app,
                    "mode": row.mode,
                    "project": row.project,
                    "raw": row.title,
                    "parsed": parsed,
                })

        row_dur_s = _rounded_number(getattr(row, "duration_s", 0), 3)
        same_group = (
            current_group is not None
            and current_group_end is not None
            and current_group["kind"] == row.kind
            and current_group.get("source") == getattr(row, "source", None)
            and current_group.get("app") == getattr(row, "app", None)
            and current_group.get("mode") == getattr(row, "mode", None)
            and current_group.get("project") == getattr(row, "project", None)
            and abs((row.start - current_group_end).total_seconds()) < 1e-6
        )
        if not same_group:
            flush_group()
            current_group = {
                "start_s": _relative_seconds(origin, row.start),
                "dur_s": row_dur_s,
                "kind": row.kind,
                "source": getattr(row, "source", None),
                "keypress_count": int(getattr(row, "keypress_count", 0) or 0),
            }
            if getattr(row, "app", None):
                current_group["app"] = row.app
            if getattr(row, "mode", None):
                current_group["mode"] = row.mode
            if getattr(row, "project", None):
                current_group["project"] = row.project
            current_group_start = row.start
            current_group_end = row.end
            current_subspans = []
        else:
            current_group["dur_s"] = _rounded_number(float(current_group["dur_s"] or 0) + float(getattr(row, "duration_s", 0) or 0), 3)
            current_group["keypress_count"] = int(current_group.get("keypress_count", 0) or 0) + int(getattr(row, "keypress_count", 0) or 0)
            current_group_end = row.end

        if title_id is not None and current_group_start is not None:
            current_subspans.append([
                _relative_seconds(current_group_start, row.start),
                row_dur_s,
                title_id,
                int(getattr(row, "keypress_count", 0) or 0),
            ])

    flush_group()

    return {
        "date": d.isoformat(),
        "contract_version": 2,
        "time_basis": {
            "origin": origin.isoformat(),
            "timezone": str(tzinfo) if tzinfo else None,
            "unit": "seconds",
            "semantics": "start_s is seconds from local midnight; dur_s is span duration in seconds.",
            "subspan_fields": ["offset_s", "dur_s", "title_id", "keypress_count"],
        },
        "summary": {
            "raw_row_count": raw_row_count,
            "row_count": len(encoded_rows),
            "title_count": len(titles),
            "kind_breakdown": dict(kinds.most_common()),
            "source_breakdown": dict(sources.most_common()),
            "top_apps": dict(apps.most_common(10)),
            "total_keypresses": total_keypresses,
            "coverage_gap_s": _rounded_number(covered_gap_s, 3),
            "healed_gap_s": _rounded_number(healed_gap_s, 3),
        },
        "titles": titles,
        "rows": encoded_rows,
    }


def _build_sleep_payload(records: list[Any], architecture: list[Any] | None = None) -> dict[str, Any]:
    summary = _summarize_sleep(records, architecture)
    notes = _sleep_quality_notes(summary)
    primary = None
    if records:
        primary = max(
            records,
            key=lambda record: (
                float(getattr(record, "confidence", 0) or 0),
                float(getattr(record, "sleep_duration_min", 0) or 0),
            ),
        )
    interpretation = {
        "notes": notes,
        "primary_record_source": getattr(primary, "source", None) if primary else None,
        "primary_record_confidence": _rounded_number(getattr(primary, "confidence", None), 2) if primary else None,
        "primary_record_evidence": list(getattr(primary, "evidence", ()) or ()) if primary else [],
    }
    return {
        "summary": summary,
        "interpretation": interpretation,
        "records": records,
        "architecture": architecture or [],
    }


def _build_ai_activity_payload(
    *,
    poly_events: list[Any],
    poly_summaries: list[Any],
    sessions: list[Any],
    transcripts: list[Any],
) -> dict[str, Any]:
    summary = _summarize_ai(poly_summaries, poly_events, sessions=sessions, transcripts=transcripts)
    prompt_text_ids: dict[str, str] = {}
    prompt_texts: list[dict[str, Any]] = []
    user_prompts = []
    dialogues = []
    for transcript in transcripts:
        prompt_ids_by_ordinal: dict[int, str] = {}
        prompts = []
        for message in transcript.messages:
            if getattr(message, "kind", getattr(message, "role", "unknown")) != "prompt" or not message.text:
                continue
            text_id = prompt_text_ids.get(message.text)
            if text_id is None:
                text_id = f"pt{len(prompt_texts) + 1:04d}"
                prompt_text_ids[message.text] = text_id
                prompt_texts.append({
                    "prompt_text_id": text_id,
                    "text": message.text,
                    "char_count": len(message.text),
                })
            prompts.append({
                "prompt_id": f"{transcript.conversation_id}:u{message.ordinal}",
                "ordinal": message.ordinal,
                "prompt_text_id": text_id,
                "approx_tokens": message.approx_tokens,
            })
        prompt_ids_by_ordinal = {prompt["ordinal"]: prompt["prompt_id"] for prompt in prompts}
        if prompts:
            user_prompts.append({
                "conversation_id": transcript.conversation_id,
                "provider": transcript.provider,
                "title": transcript.title,
                "first_message_at": transcript.first_message_at,
                "prompt_count": len(prompts),
                "token_estimate": transcript.user_prompt_tokens,
                "prompts": prompts,
            })

        dialogue_messages = []
        for message in transcript.messages:
            kind = getattr(message, "kind", getattr(message, "role", "unknown"))
            if kind not in {"prompt", "assistant"} or not message.text:
                continue
            row = {
                "ordinal": message.ordinal,
                "role": message.role,
                "approx_tokens": message.approx_tokens,
            }
            if kind == "prompt":
                row["prompt_id"] = prompt_ids_by_ordinal.get(message.ordinal)
            else:
                row["text"] = message.text
            if message.has_tool_use:
                row["has_tool_use"] = True
            if message.has_thinking:
                row["has_thinking"] = True
            dialogue_messages.append(row)
        if dialogue_messages:
            dialogues.append({
                "conversation_id": transcript.conversation_id,
                "provider": transcript.provider,
                "title": transcript.title,
                "first_message_at": transcript.first_message_at,
                "last_message_at": transcript.last_message_at,
                "token_estimates": {
                    "user_prompts": transcript.user_prompt_tokens,
                    "dialogue": transcript.dialogue_tokens,
                    "all_messages": transcript.all_message_tokens,
                },
                "messages": dialogue_messages,
            })

    session_rows = []
    transcript_by_id = {transcript.conversation_id: transcript for transcript in transcripts}
    for session in sessions:
        transcript = transcript_by_id.get(getattr(session, "conversation_id", ""))
        if transcript is not None:
            token_estimates = {
                "user_prompts": transcript.user_prompt_tokens,
                "dialogue": transcript.dialogue_tokens,
                "all_messages": transcript.all_message_tokens,
            }
        else:
            token_estimates = None
        cost_status = "estimated" if getattr(session, "cost_is_estimated", False) else "measured"
        if cost_status == "estimated" and not float(getattr(session, "total_cost_usd", 0) or 0):
            cost_status = "estimated_zero"
        session_rows.append({
            "conversation_id": session.conversation_id,
            "provider": session.provider,
            "title": session.title,
            "canonical_session_date": session.canonical_session_date,
            "first_message_at": session.first_message_at,
            "last_message_at": session.last_message_at,
            "message_count": session.message_count,
            "substantive_count": session.substantive_count,
            "attachment_count": session.attachment_count,
            "work_event_count": session.work_event_count,
            "phase_count": session.phase_count,
            "word_count": session.word_count,
            "tool_use_count": session.tool_use_count,
            "thinking_count": session.thinking_count,
            "work_event_kind": session.work_event_kind,
            "work_event_projects": session.work_event_projects,
            "auto_tags": session.auto_tags,
            "cost_status": cost_status,
            "display_cost_usd": None if cost_status.startswith("estimated") else _rounded_number(session.total_cost_usd, 4),
            "recorded_cost_usd": _rounded_number(session.total_cost_usd, 4) if cost_status == "measured" else None,
            "estimated_cost_usd": _rounded_number(session.total_cost_usd, 4) if cost_status == "estimated" else None,
            "token_estimates": token_estimates,
        })

    return {
        "summary": summary,
        "sessions": session_rows,
        "work_events": poly_events,
        "prompt_texts": prompt_texts,
        "user_prompts": user_prompts,
        "dialogues": dialogues,
    }


def _read_field(payload: Any, name: str, default: Any = None) -> Any:
    if isinstance(payload, dict):
        return payload.get(name, default)
    return getattr(payload, name, default)


def _rounded_number(value: Any, digits: int = 2) -> int | float | None:
    if value is None:
        return None
    rounded = round(float(value), digits)
    return int(rounded) if float(rounded).is_integer() else rounded


def _extract_commit_refs(subject: str) -> dict[str, list[int]]:
    text = subject or ""
    pr_refs = sorted({int(match) for match in re.findall(r"\(#(\d+)\)", text)})
    issue_refs = sorted({
        int(match)
        for match in re.findall(r"\b(?:close|closes|closed|fix|fixes|fixed|ref|refs)\s+#(\d+)\b", text, flags=re.IGNORECASE)
    })
    return {
        "prs": pr_refs,
        "issues": issue_refs,
    }


def _subject_prefix(subject: str) -> str | None:
    lowered = (subject or "").strip().lower()
    for prefix in ("feat", "fix", "refactor", "test", "docs", "chore", "perf", "ci", "build", "style", "archive"):
        if lowered.startswith(f"{prefix}:") or lowered.startswith(f"{prefix}("):
            return prefix
    return None


def _build_commit_payload(
    *,
    facts: list[Any],
    sessions: list[Any],
    daily: list[Any],
) -> dict[str, Any]:
    fact_rows = []
    for fact in facts:
        subject = getattr(fact, "subject", "") or ""
        refs = _extract_commit_refs(subject)
        fact_rows.append({
            "repo": getattr(fact, "repo", None),
            "commit": getattr(fact, "commit", None),
            "authored_at": getattr(fact, "authored_at", None),
            "author": getattr(fact, "author", None),
            "subject": subject,
            "subject_prefix": _subject_prefix(subject),
            "refs": refs if any(refs.values()) else None,
            "lines_added": getattr(fact, "lines_added", None),
            "lines_deleted": getattr(fact, "lines_deleted", None),
            "lines_changed": getattr(fact, "lines_changed", None),
            "files_changed": getattr(fact, "files_changed", None),
            "paths": getattr(fact, "paths", None),
            "path_roots": getattr(fact, "path_roots", None),
        })
    return {
        "facts": fact_rows,
        "sessions": sessions,
        "daily": daily,
    }


def _filter_github_context_for_facts(context: Any, facts: list[Any]) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {"status": "unavailable", "items": []}
    wanted: set[tuple[str, str, int]] = set()
    for fact in facts:
        repo = str(getattr(fact, "repo", "") or "")
        refs = _extract_commit_refs(getattr(fact, "subject", "") or "")
        wanted.update((repo, "pr", number) for number in refs["prs"])
        wanted.update((repo, "issue", number) for number in refs["issues"] if number not in refs["prs"])
    items = [
        item
        for item in context.get("items", []) or []
        if isinstance(item, dict)
        and (str(item.get("repo") or ""), str(item.get("kind") or ""), int(item.get("number") or 0)) in wanted
    ]
    return {
        "status": context.get("status", "ok") if items else "no_refs",
        "items": items,
    }


def _build_clipboard_payload(rows: list[Any]) -> dict[str, Any]:
    kinds = Counter(getattr(row, "kind", "unknown") for row in rows)
    sources = Counter(getattr(row, "source", "unknown") for row in rows)
    value_ids: dict[str, str] = {}
    values: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for row in rows:
        value = str(getattr(row, "value", "") or "")
        value_id = value_ids.get(value)
        if value_id is None:
            value_id = f"v{len(values) + 1:04d}"
            value_ids[value] = value_id
            values.append({
                "value_id": value_id,
                "value": value,
                "char_count": len(value),
            })
        entries.append({
            "recorded_at": getattr(row, "recorded_at", None),
            "value_id": value_id,
            "source": getattr(row, "source", None),
            "file_path": getattr(row, "file_path", None),
            "pinned": getattr(row, "pinned", False),
            "kind": getattr(row, "kind", "unknown"),
        })
    return {
        "summary": {
            "entry_count": len(rows),
            "unique_value_count": len(values),
            "kind_breakdown": dict(kinds.most_common()),
            "source_files": dict(sources.most_common()),
            "semantics": "Clipboard entries preserve timestamp/source metadata; exact values are stored verbatim once in values[] and referenced by value_id.",
        },
        "values": values,
        "entries": entries,
    }


def _build_irc_payload(rows: list[Any]) -> dict[str, Any]:
    return {
        "summary": {
            "conversation_count": len(rows),
            "total_lines": sum(int(getattr(row, "total_lines", 0) or 0) for row in rows),
            "sinity_lines": sum(int(getattr(row, "sinity_lines", 0) or 0) for row in rows),
            "mention_lines": sum(int(getattr(row, "mention_lines", 0) or 0) for row in rows),
            "channels": dict(Counter(getattr(row, "channel", "unknown") for row in rows).most_common()),
            "semantics": "IRC excerpts preserve surrounding dialogue for conversations containing sinity lines or direct mentions.",
        },
        "conversations": rows,
    }


def _build_raw_log_payload(rows: list[Any]) -> dict[str, Any]:
    return {
        "summary": {
            "entry_count": len(rows),
            "source_files": dict(Counter(getattr(row, "source_path", "unknown") for row in rows).most_common()),
            "semantics": "Knowledgebase raw-log entries are exact timestamped captures, not retrospective summaries.",
        },
        "entries": rows,
    }


def _top_items_from_mapping(
    mapping: dict[str, Any] | Counter | None,
    *,
    limit: int = 5,
    value_key: str = "value",
    digits: int = 2,
) -> list[dict[str, Any]]:
    if not mapping:
        return []
    ranked: list[tuple[str, float]] = []
    for name, value in mapping.items():
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric <= 0:
            continue
        ranked.append((str(name), numeric))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return [
        {"name": name, value_key: _rounded_number(value, digits)}
        for name, value in ranked[:limit]
    ]


def _top_rows_by_metric(
    rows: list[dict[str, Any]] | None,
    field: str,
    *,
    limit: int = 3,
    digits: int = 2,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    ranked = [row for row in rows if isinstance(row, dict) and row.get(field) not in (None, "")]
    ranked.sort(key=lambda row: float(row[field]), reverse=True)
    result: list[dict[str, Any]] = []
    for row in ranked[:limit]:
        item: dict[str, Any] = {"date": row.get("date"), field: _rounded_number(row.get(field), digits)}
        if row.get("dominant_project"):
            item["dominant_project"] = row["dominant_project"]
        if row.get("dominant_mode"):
            item["dominant_mode"] = row["dominant_mode"]
        result.append(item)
    return result


def _aggregate_nested_counts(nested: dict[str, dict[str, int]] | None) -> Counter:
    total: Counter = Counter()
    if not nested:
        return total
    for inner in nested.values():
        if isinstance(inner, dict):
            total.update(inner)
    return total


def _sum_nested_outer_counts(nested: dict[str, dict[str, int]] | None) -> Counter:
    total: Counter = Counter()
    if not nested:
        return total
    for outer, inner in nested.items():
        if isinstance(inner, dict):
            total[str(outer)] += sum(int(v or 0) for v in inner.values())
    return total


def _baseline_flags(baseline: dict[str, Any] | None) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    if not baseline:
        return flags
    for metric, payload in baseline.items():
        if not isinstance(payload, dict):
            continue
        flag = payload.get("flag")
        if not flag or flag == "typical":
            continue
        flags.append({
            "metric": metric,
            "flag": flag,
            "today": payload.get("today"),
            "avg_7d": payload.get("avg_7d"),
            "avg_30d": payload.get("avg_30d"),
        })
    return flags


def _sleep_quality_notes(summary: dict[str, Any] | None) -> list[str]:
    if not summary:
        return []
    notes: list[str] = []
    if summary.get("low_confidence_records"):
        notes.append(
            f"Sleep inference includes {summary['low_confidence_records']} low-confidence record(s); treat sleep timing as suggestive rather than exact."
        )
    avg_overlap = summary.get("avg_aw_active_overlap_pct")
    if avg_overlap is not None and float(avg_overlap) >= 25:
        notes.append(
            f"Sleep windows overlap ActivityWatch active time by {avg_overlap}% on average, so wakefulness vs stale AW state needs caution."
        )
    keypresses = summary.get("sleep_window_keypresses")
    if keypresses:
        notes.append(
            f"Sleep windows include {keypresses} keypresses, which points to probable false-positive or mixed sleep labeling in at least some intervals."
        )
    evidence_names = set((summary.get("evidence") or {}).keys())
    if any("media" in name for name in evidence_names):
        notes.append("Ambient media overlaps some sleep windows, so autoplay/background listening may explain part of the nocturnal activity signal.")
    return notes


def _trend_hooks(trends: dict[str, Any] | None, *, limit: int = 5) -> list[dict[str, Any]]:
    hooks: list[dict[str, Any]] = []
    if not trends:
        return hooks
    for metric, payload in trends.items():
        direction = _read_field(payload, "direction")
        significant = bool(_read_field(payload, "significant", False))
        if not significant and direction in (None, "stable", "normal"):
            continue
        hooks.append({
            "metric": metric,
            "direction": direction,
            "significant": significant,
            "slope": _rounded_number(_read_field(payload, "slope"), 3),
            "p_value": _rounded_number(_read_field(payload, "p_value"), 4),
        })
    hooks.sort(key=lambda item: (not item["significant"], abs(float(item["slope"] or 0))), reverse=False)
    hooks.sort(key=lambda item: (not item["significant"], -abs(float(item["slope"] or 0))))
    return hooks[:limit]


def _anomaly_hooks(anomalies: dict[str, Any] | None, *, limit: int = 5) -> list[dict[str, Any]]:
    hooks: list[dict[str, Any]] = []
    if not anomalies:
        return hooks
    for metric, payload in anomalies.items():
        if not _read_field(payload, "is_anomaly", False):
            continue
        hooks.append({
            "metric": metric,
            "direction": _read_field(payload, "direction"),
            "value": _rounded_number(_read_field(payload, "value"), 2),
            "score": _rounded_number(_read_field(payload, "score"), 2),
        })
    hooks.sort(key=lambda item: abs(float(item["score"] or 0)), reverse=True)
    return hooks[:limit]


def _regime_change_hooks(changes: list[Any] | None, *, limit: int = 5) -> list[dict[str, Any]]:
    hooks: list[dict[str, Any]] = []
    if not changes:
        return hooks
    for item in changes:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        metric, payload = item
        hooks.append({
            "metric": str(metric),
            "index": _read_field(payload, "index"),
            "before_mean": _rounded_number(_read_field(payload, "before_mean"), 2),
            "after_mean": _rounded_number(_read_field(payload, "after_mean"), 2),
            "magnitude": _rounded_number(_read_field(payload, "magnitude"), 3),
        })
    hooks.sort(key=lambda item: abs(float(item["magnitude"] or 0)), reverse=True)
    return hooks[:limit]


def _driver_hooks(drivers: list[Any] | None, *, limit: int = 5) -> list[dict[str, Any]]:
    hooks: list[dict[str, Any]] = []
    if not drivers:
        return hooks
    for item in drivers:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        factor, payload = item
        significant = bool(_read_field(payload, "significant", False))
        if not significant:
            continue
        hooks.append({
            "factor": str(factor),
            "lag": _read_field(payload, "lag"),
            "r": _rounded_number(_read_field(payload, "r"), 4),
            "p_value": _rounded_number(_read_field(payload, "p_value"), 4),
            "n": _read_field(payload, "n"),
        })
    hooks.sort(key=lambda item: abs(float(item["r"] or 0)), reverse=True)
    return hooks[:limit]


def _signal(kind: str, summary: str, **evidence: Any) -> dict[str, Any]:
    clean_evidence = {k: v for k, v in evidence.items() if v not in (None, [], {}, "")}
    return {"kind": kind, "summary": summary, "evidence": clean_evidence}


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _build_day_narrative_brief(
    d: date,
    *,
    active_h: float,
    seg,
    git_act: list,
    facts: list,
    poly_events: list,
    poly_summaries: list,
    poly_sessions: list,
    poly_transcripts: list,
    shells: list,
    sleep_data: list,
    work_sess: list,
    clipboard_day: list,
    irc_day: list,
    raw_log_day: list,
    baseline: dict[str, Any],
    two_track,
) -> dict[str, Any]:
    contexts = _top_items_from_mapping(getattr(seg, "context_hours", {}) if seg else {}, value_key="hours", digits=2)
    git_projects = _top_items_from_mapping(Counter(getattr(g, "repo", "unknown") for g in git_act if getattr(g, "repo", None)), value_key="commits", digits=0)
    work_minutes: Counter = Counter()
    for session in work_sess or []:
        if getattr(session, "project", None):
            work_minutes[str(session.project)] += float(getattr(session, "duration_min", 0) or 0)
    work_projects = _top_items_from_mapping(work_minutes, value_key="minutes", digits=1)
    sleep_summary = _summarize_sleep(sleep_data)
    ai_summary = _summarize_ai(poly_summaries, poly_events, sessions=poly_sessions, transcripts=poly_transcripts)
    ai_providers = _top_items_from_mapping(ai_summary.get("providers", {}), value_key="sessions", digits=0)
    ai_repos = _top_items_from_mapping(ai_summary.get("repos_active", {}), value_key="days", digits=0)
    quality_notes = _sleep_quality_notes(sleep_summary)
    if poly_summaries and not poly_events:
        quality_notes.append("Polylogue recorded sessions but no work-event rows, so session summaries carry more signal than work-event absence.")
    if ai_summary.get("cost", {}).get("status") == "estimated_zero" and ai_summary.get("session_count"):
        quality_notes.append("Polylogue cost rows are estimated-zero on this day, so visible spend should be treated as unknown rather than literally zero.")
    if active_h <= 0 and (facts or shells or poly_summaries):
        quality_notes.append("ActivityWatch coverage is sparse relative to other evidence on this day.")

    baseline_flags = _baseline_flags(baseline)
    signals: list[dict[str, Any]] = []
    angles: list[str] = []

    commits = int(getattr(two_track, "commit_count", 0) or 0) if two_track else sum(getattr(g, "commit_count", 0) for g in git_act)
    shell_commands = int(getattr(two_track, "shell_commands", 0) or 0) if two_track else sum(getattr(s, "command_count", 0) for s in shells)
    ai_sessions = int(getattr(two_track, "ai_session_count", 0) or 0) if two_track else sum(getattr(s, "session_count", 0) for s in poly_summaries)
    sleep_hours = getattr(two_track, "sleep_hours", None) if two_track else sleep_summary.get("avg_bed_hours")
    overlaps = len(getattr(two_track, "overlaps", ()) or ()) if two_track else 0

    if sleep_hours is not None and sleep_hours < 5 and (active_h >= 10 or commits >= 15):
        signals.append(_signal("contrast", "Short sleep sits against an unusually productive day.", sleep_hours=_rounded_number(sleep_hours, 1), active_hours=_rounded_number(active_h, 2), commits=commits))
        angles.append("Center the contrast between sleep debt and sustained output.")
    if active_h >= 12:
        signals.append(_signal("duration", "This is a very long active day.", active_hours=_rounded_number(active_h, 2)))
    if commits >= 20:
        signals.append(_signal("shipping", "The day has a strong execution or shipping signature.", commits=commits))
    if ai_sessions and shell_commands and commits and shell_commands < max(commits * 1.5, 20):
        signals.append(_signal("ai_assistance", "Commit volume is high relative to shell activity, which suggests heavy AI assistance or batch commit flow.", ai_sessions=ai_sessions, shell_commands=shell_commands, commits=commits))
        angles.append("Look for how AI assistance changed the human/manual texture of the day.")
    if overlaps:
        signals.append(_signal("overlap", "Human attention and AI activity overlap materially within the day.", overlaps=overlaps, ai_blocks=len(getattr(two_track, "ai_blocks", ()) or ()) if two_track else len(poly_events)))
        angles.append("Treat human and AI activity as interleaved tracks rather than separate narratives.")
    if contexts and contexts[0]["name"] != "work" and commits >= 10:
        signals.append(_signal("contextual_contrast", f"Most visible attention sat in '{contexts[0]['name']}' even though output remained high.", dominant_context=contexts[0]["name"], dominant_context_hours=contexts[0]["hours"], commits=commits))
        angles.append(f"Treat the day as output embedded inside a {contexts[0]['name']}-heavy envelope, not as a simple workday.")
    for flag in baseline_flags[:3]:
        signals.append(_signal("baseline", f"{flag['metric']} is {flag['flag']} against recent baseline.", metric=flag["metric"], today=flag["today"], avg_7d=flag["avg_7d"], avg_30d=flag["avg_30d"]))
    if quality_notes:
        angles.append("Keep explicit caveats where sleep or AI-work-event coverage is noisy.")

    carry_forward = _dedupe_strings([item["name"] for item in git_projects[:3] + work_projects[:3] + ai_repos[:3]])
    return {
        "scale": "day",
        "key": d.isoformat(),
        "evidence_profile": {
            "sources_present": _dedupe_strings([
                "activitywatch" if seg or active_h > 0 else "",
                "git" if facts or git_act else "",
                "polylogue" if poly_summaries or poly_events else "",
                "terminal" if shells else "",
                "sleep" if sleep_data else "",
                "work_sessions" if work_sess else "",
                "clipboard" if clipboard_day else "",
                "irc" if irc_day else "",
                "raw_log" if raw_log_day else "",
            ]),
            "counts": {
                "human_segments": len(getattr(seg, "segments", ()) or ()) if seg else 0,
                "commits": commits,
                "git_facts": len(facts or []),
                "ai_blocks": len(getattr(two_track, "ai_blocks", ()) or ()) if two_track else len(poly_events or []),
                "ai_sessions": ai_sessions,
                "shell_sessions": len(shells or []),
                "work_sessions": len(work_sess or []),
                "sleep_records": len(sleep_data or []),
                "polylogue_sessions": len(poly_sessions or []),
                "clipboard_entries": len(clipboard_day or []),
                "irc_conversations": len(irc_day or []),
                "raw_log_entries": len(raw_log_day or []),
            },
            "data_quality_notes": quality_notes,
        },
        "dominant_threads": {
            "contexts": contexts,
            "git_projects": git_projects,
            "work_session_projects": work_projects,
            "ai_providers": ai_providers,
            "ai_repos": ai_repos,
        },
        "analytic_hooks": {
            "baseline_flags": baseline_flags,
            "sleep_caveats": quality_notes,
            "ai_token_estimates": ai_summary.get("token_estimates"),
            "top_work_sessions": [
                {
                    "project": getattr(session, "project", None),
                    "duration_min": _rounded_number(getattr(session, "duration_min", None), 1),
                }
                for session in sorted(work_sess or [], key=lambda item: float(getattr(item, "duration_min", 0) or 0), reverse=True)[:5]
            ],
        },
        "story_signals": signals,
        "angles": _dedupe_strings(angles),
        "carry_forward": carry_forward,
    }


def _build_week_narrative_brief(
    week_key: str,
    *,
    start: date,
    end: date,
    day_metrics: list[dict[str, Any]],
    total_commits: int,
    project_commits: Counter,
    kind_dist: Counter,
    rhythm,
    trends: dict[str, Any],
    sleep_summary: dict[str, Any],
    ai_summary: dict[str, Any],
) -> dict[str, Any]:
    project_items = _top_items_from_mapping(project_commits, value_key="commits", digits=0)
    provider_items = _top_items_from_mapping(ai_summary.get("providers", {}), value_key="sessions", digits=0)
    mode_counts = Counter(row.get("dominant_mode") for row in day_metrics if row.get("dominant_mode"))
    mode_items = _top_items_from_mapping(mode_counts, value_key="days", digits=0)
    quality_notes = _sleep_quality_notes(sleep_summary)
    if ai_summary.get("session_count") and not kind_dist:
        quality_notes.append("AI sessions are present but work-event rows are sparse, so session summaries are stronger than work-event absence.")
    signals: list[dict[str, Any]] = []
    angles: list[str] = []
    top_commit_days = _top_rows_by_metric(day_metrics, "commits", limit=3, digits=0)
    top_active_days = _top_rows_by_metric(day_metrics, "active_hours", limit=3, digits=2)
    trend_hooks = _trend_hooks(trends)
    rhythm_payload = to_dict(rhythm) if rhythm else {}

    if project_items and total_commits and float(project_items[0]["commits"]) >= total_commits * 0.5:
        signals.append(_signal("campaign", "One project accounts for most of the week's commit energy.", project=project_items[0]["name"], commits=project_items[0]["commits"], total_commits=total_commits))
        angles.append(f"Write this as a campaign week centered on {project_items[0]['name']}.")
    if top_commit_days and total_commits and float(top_commit_days[0]["commits"] or 0) >= total_commits * 0.4:
        signals.append(_signal("burst", "A single day carries an outsized share of the week's commits.", date=top_commit_days[0]["date"], commits=top_commit_days[0]["commits"], total_commits=total_commits))
        angles.append(f"Use {top_commit_days[0]['date']} as the week's hinge point instead of flattening the days together.")
    if ai_summary.get("session_count", 0) >= 10 or ai_summary.get("total_messages", 0) >= 1000:
        signals.append(_signal("ai_heavy", "AI use is dense enough to be part of the week's main story.", session_count=ai_summary.get("session_count"), total_messages=ai_summary.get("total_messages")))
        angles.append("Track how AI participation changes across the week rather than mentioning it once as metadata.")
    if rhythm_payload.get("best_day") and rhythm_payload.get("worst_day") and rhythm_payload.get("best_day") != rhythm_payload.get("worst_day"):
        signals.append(_signal("rhythm", "The week has a clear weekday asymmetry.", best_day=rhythm_payload.get("best_day"), worst_day=rhythm_payload.get("worst_day"), consistency=rhythm_payload.get("consistency")))
        angles.append("Use the week's shape and asymmetry instead of reporting an average week.")
    if quality_notes:
        angles.append("Keep sleep and AI-work-event caveats explicit where they materially change interpretation.")

    carry_forward = _dedupe_strings([item["name"] for item in project_items[:4] + provider_items[:3]])
    return {
        "scale": "week",
        "key": week_key,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "evidence_profile": {
            "days_with_active_hours": sum(1 for row in day_metrics if row.get("active_hours") is not None),
            "days_with_commits": sum(1 for row in day_metrics if row.get("commits")),
            "days_with_sleep": sum(1 for row in day_metrics if row.get("sleep_hours") is not None),
            "data_quality_notes": quality_notes,
        },
        "shape": {
            "best_day": rhythm_payload.get("best_day"),
            "worst_day": rhythm_payload.get("worst_day"),
            "consistency": rhythm_payload.get("consistency"),
            "top_commit_days": top_commit_days,
            "top_active_days": top_active_days,
            "dominant_modes": mode_items,
        },
        "dominant_threads": {
            "projects": project_items,
            "ai_providers": provider_items,
            "work_event_kinds": _top_items_from_mapping(kind_dist, value_key="events", digits=0),
        },
        "analytic_hooks": {
            "trend_hooks": trend_hooks,
            "sleep_caveats": quality_notes,
        },
        "story_signals": signals,
        "angles": _dedupe_strings(angles),
        "carry_forward": carry_forward,
    }


def _build_month_narrative_brief(
    month_key: str,
    *,
    start: date,
    end: date,
    day_metrics: list[dict[str, Any]],
    per_week_commits: dict[str, int],
    project_by_week: dict[str, dict[str, int]],
    rhythm,
    drivers: list[Any],
    anomalies: dict[str, Any],
    regime_changes: list[Any],
    trends: dict[str, Any],
    sleep_summary: dict[str, Any],
    ai_summary: dict[str, Any],
) -> dict[str, Any]:
    project_totals = _aggregate_nested_counts(project_by_week)
    project_items = _top_items_from_mapping(project_totals, value_key="commits", digits=0)
    provider_items = _top_items_from_mapping(ai_summary.get("providers", {}), value_key="sessions", digits=0)
    quality_notes = _sleep_quality_notes(sleep_summary)
    if ai_summary.get("session_count") and not ai_summary.get("work_event_breakdown"):
        quality_notes.append("AI session summaries have much better coverage than work-event kinds for this month.")
    regime_hooks = _regime_change_hooks(regime_changes)
    driver_hooks = _driver_hooks(drivers)
    anomaly_hooks = _anomaly_hooks(anomalies)
    trend_hooks = _trend_hooks(trends)
    top_weeks = _top_items_from_mapping(per_week_commits, value_key="commits", digits=0)
    top_commit_days = _top_rows_by_metric(day_metrics, "commits", limit=5, digits=0)
    rhythm_payload = to_dict(rhythm) if rhythm else {}
    signals: list[dict[str, Any]] = []
    angles: list[str] = []

    total_commits = sum(int(v or 0) for v in per_week_commits.values())
    if regime_hooks:
        signals.append(_signal("regime_shift", "The month appears to split into distinct acts or regimes.", strongest_metric=regime_hooks[0]["metric"], magnitude=regime_hooks[0]["magnitude"]))
        angles.append("Structure the month around regime changes and act transitions rather than day-by-day recital.")
    if project_items and total_commits and float(project_items[0]["commits"]) >= total_commits * 0.4:
        signals.append(_signal("campaign", "A single project dominates much of the month's visible shipping.", project=project_items[0]["name"], commits=project_items[0]["commits"], total_commits=total_commits))
        angles.append(f"Thread {project_items[0]['name']} through the month as a primary arc.")
    if driver_hooks:
        signals.append(_signal("cross_signal", "There are statistically notable cross-signal relationships worth interpreting cautiously.", factor=driver_hooks[0]["factor"], r=driver_hooks[0]["r"], lag=driver_hooks[0]["lag"]))
        angles.append("Use the driver relationships as interpretive hints, not deterministic causal claims.")
    if anomaly_hooks:
        signals.append(_signal("anomaly", "The month contains at least one standout metric anomaly.", metric=anomaly_hooks[0]["metric"], direction=anomaly_hooks[0]["direction"]))
    if quality_notes:
        angles.append("Keep coverage and sleep-confidence caveats visible where they change the reading of the month.")

    carry_forward = _dedupe_strings([item["name"] for item in project_items[:5] + provider_items[:3]])
    return {
        "scale": "month",
        "key": month_key,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "evidence_profile": {
            "days_with_active_hours": sum(1 for row in day_metrics if row.get("active_hours") is not None),
            "days_with_commits": sum(1 for row in day_metrics if row.get("commits")),
            "days_with_sleep": sum(1 for row in day_metrics if row.get("sleep_hours") is not None),
            "data_quality_notes": quality_notes,
        },
        "shape": {
            "best_day": rhythm_payload.get("best_day"),
            "worst_day": rhythm_payload.get("worst_day"),
            "top_weeks": top_weeks,
            "top_commit_days": top_commit_days,
        },
        "dominant_threads": {
            "projects": project_items,
            "ai_providers": provider_items,
        },
        "analytic_hooks": {
            "driver_hooks": driver_hooks,
            "anomaly_hooks": anomaly_hooks,
            "regime_change_hooks": regime_hooks,
            "trend_hooks": trend_hooks,
            "sleep_caveats": quality_notes,
        },
        "story_signals": signals,
        "angles": _dedupe_strings(angles),
        "carry_forward": carry_forward,
    }


def _build_rollup_narrative_brief(
    scale: str,
    key: str,
    *,
    start: date,
    end: date,
    per_unit: list[dict[str, Any]],
    unit_key: str,
    project_counts: Counter,
    ai_summary: dict[str, Any],
    sleep_summary: dict[str, Any],
    trends: dict[str, Any],
) -> dict[str, Any]:
    project_items = _top_items_from_mapping(project_counts, value_key="commits", digits=0)
    provider_items = _top_items_from_mapping(ai_summary.get("providers", {}), value_key="sessions", digits=0)
    top_units = sorted(
        [
            row for row in per_unit
            if (row.get("commits") not in (None, "") and float(row.get("commits") or 0) > 0)
            or (row.get("active_hours") not in (None, "") and float(row.get("active_hours") or 0) > 0)
        ],
        key=lambda row: float(row.get("commits") or 0),
        reverse=True,
    )[:4]
    quality_notes = _sleep_quality_notes(sleep_summary)
    trend_hooks = _trend_hooks(trends)
    signals: list[dict[str, Any]] = []
    angles: list[str] = []
    total_commits = sum(int(row.get("commits") or 0) for row in per_unit)

    if project_items and total_commits and float(project_items[0]["commits"]) >= total_commits * 0.35:
        signals.append(_signal("campaign", f"A small set of projects dominate this {scale}.", project=project_items[0]["name"], commits=project_items[0]["commits"], total_commits=total_commits))
        angles.append(f"Use the dominant project arcs to organize the {scale}, not just calendar buckets.")
    if top_units and total_commits and float(top_units[0].get("commits") or 0) >= total_commits * 0.4:
        signals.append(_signal("burst", f"One {unit_key} carries a disproportionate share of commits.", unit=top_units[0].get(unit_key), commits=top_units[0].get("commits"), total_commits=total_commits))
        angles.append(f"Treat {top_units[0].get(unit_key)} as a hinge point inside the {scale}.")
    if trend_hooks:
        signals.append(_signal("trend", f"There are directional shifts worth tracing across the {scale}.", metric=trend_hooks[0]["metric"], direction=trend_hooks[0]["direction"]))
        angles.append(f"Anchor the {scale} narrative in directional change, not only in totals.")
    if quality_notes:
        angles.append("Keep sleep-confidence caveats visible where long-range recovery claims matter.")

    carry_forward = _dedupe_strings([item["name"] for item in project_items[:5] + provider_items[:3]])
    return {
        "scale": scale,
        "key": key,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "shape": {"top_units": top_units},
        "dominant_threads": {
            "projects": project_items,
            "ai_providers": provider_items,
        },
        "analytic_hooks": {
            "trend_hooks": trend_hooks,
            "sleep_caveats": quality_notes,
        },
        "story_signals": signals,
        "angles": _dedupe_strings(angles),
        "carry_forward": carry_forward,
    }


def _build_overview_narrative_brief(
    *,
    start: date,
    end: date,
    source_coverage: dict[str, dict[str, bool]],
    project_arcs: dict[str, dict[str, int]],
    provider_months: dict[str, dict[str, int]],
    trends: dict[str, Any],
    regime_changes: list[Any],
    sleep_patterns: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    project_items = _top_items_from_mapping(_sum_nested_outer_counts(project_arcs), value_key="commits", digits=0)
    provider_items = _top_items_from_mapping(_sum_nested_outer_counts(provider_months), value_key="sessions", digits=0)
    trend_hooks = _trend_hooks(trends, limit=8)
    regime_hooks = _regime_change_hooks(regime_changes, limit=8)
    source_months: dict[str, int] = Counter()
    for month, coverage in source_coverage.items():
        for source, present in coverage.items():
            if present:
                source_months[source] += 1
    coverage_items = _top_items_from_mapping(source_months, value_key="months", digits=0)
    sleep_extremes = []
    if sleep_patterns:
        ranked_sleep = sorted(
            [
                {"month": month, "avg_hours": details.get("avg_hours"), "n_nights": details.get("n_nights")}
                for month, details in sleep_patterns.items()
                if details.get("avg_hours") is not None
            ],
            key=lambda item: float(item["avg_hours"]),
        )
        sleep_extremes = ranked_sleep[:2] + ranked_sleep[-2:]
    signals: list[dict[str, Any]] = []
    angles: list[str] = []
    if regime_hooks:
        signals.append(_signal("era_shift", "The overall history contains major regime transitions.", strongest_metric=regime_hooks[0]["metric"], magnitude=regime_hooks[0]["magnitude"]))
        angles.append("Write the long-range narrative as eras and regime shifts, not as an annual ledger.")
    if project_items:
        signals.append(_signal("project_arc", "A small set of projects dominate the long-run visible output.", project=project_items[0]["name"], commits=project_items[0]["commits"]))
        angles.append("Treat the major projects as recurring threads that wax and wane over years.")
    if provider_items:
        signals.append(_signal("ai_evolution", "Provider mix changes enough to be part of the long-range story.", provider=provider_items[0]["name"], sessions=provider_items[0]["sessions"]))
        angles.append("Use provider evolution to explain shifts in AI-assisted work style over time.")
    return {
        "scale": "overview",
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "dominant_threads": {
            "projects": project_items,
            "ai_providers": provider_items,
            "source_coverage": coverage_items,
        },
        "analytic_hooks": {
            "trend_hooks": trend_hooks,
            "regime_change_hooks": regime_hooks,
            "sleep_extremes": sleep_extremes,
        },
        "story_signals": signals,
        "angles": _dedupe_strings(angles),
        "carry_forward": _dedupe_strings([item["name"] for item in project_items[:6] + provider_items[:4]]),
    }


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

    def __init__(self, start: date, end: date, coverage: dict[str, DateSpan] | None = None):
        from ..sources.activitywatch import (
            active_seconds_by_date,
        )
        from ..sources.git import commit_facts, daily_activity as git_daily, commit_sessions, github_context_for_commits
        from ..sources.polylogue import (
            work_events,
            day_session_summaries,
            session_profiles_for_date,
            conversation_transcripts,
        )
        from ..sources.terminal import shell_sessions
        from ..sources.sleep_infer import infer_sleep
        from ..sources.health import (
            daily_steps, daily_health_summary, heart_rate_measurements, daily_stress,
            calorie_burns, nap_sessions, activity_summaries, movement_records,
            ecg_measurements,
        )
        from ..sources.sleep import sleep_stages, sleep_architecture
        from ..sources.web import daily_browsing
        from ..sources.exports import daily_messenger_activity, daily_raindrop_activity
        from ..sources.substance import entries as substance_entries
        from ..sources.clipboard import entries_in_range as clipboard_entries
        from ..sources.irc import conversations_in_range as irc_conversations
        from ..sources.raw_log import entries_in_range as raw_log_entries

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
        self.aw_active = _load_date_range("AW active", active_seconds_by_date, "aw", coverage, start, end, default={}, positional=True)
        aw_derived = _load_aw_derived(start, end, coverage, self.aw_active if isinstance(self.aw_active, dict) else None)
        self.focus_spans = aw_derived.focus_spans
        self.app_sessions = aw_derived.app_sessions
        self.deep_work = aw_derived.deep_work
        self.sustained_focus = aw_derived.sustained_focus
        self.fragmentation = aw_derived.fragmentation
        self.attention = aw_derived.attention
        self.circadian = aw_derived.circadian
        # Git
        self.git_facts = _load_date_range("Git facts", commit_facts, "git", coverage, start, end)
        self.git_daily = _load_date_range("Git daily", git_daily, "git", coverage, start, end)
        self.git_sessions = _load_date_range("Git sessions", commit_sessions, "git", coverage, start, end)
        self.github_context = _safe(github_context_for_commits, self.git_facts, default={"status": "unavailable", "items": []})
        # Polylogue
        self.poly_events = _load_date_range("Polylogue events", work_events, "polylogue_events", coverage, start, end)
        self.poly_summaries = _load_date_range("Polylogue summaries", day_session_summaries, "polylogue", coverage, start, end)
        self.poly_sessions = _load_date_range("Polylogue sessions", session_profiles_for_date, "polylogue", coverage, start, end)
        self.poly_transcripts = _load_date_range("Polylogue transcripts", conversation_transcripts, "polylogue", coverage, start, end)
        # Terminal
        self.shell_sessions = _load_datetime_range("Terminal", shell_sessions, "terminal", coverage, start, end)
        # Sleep
        self.sleep = _load_date_range("Sleep", infer_sleep, "sleep", coverage, start, end, pad_start_days=1)
        self.sleep_stages = _load_date_range("Sleep stages", sleep_stages, "sleep_stages", coverage, start, end, pad_start_days=1)
        self.sleep_architecture = _load_date_range("Sleep architecture", sleep_architecture, "sleep_architecture", coverage, start, end, pad_start_days=1)
        # Health
        self.steps = _load_date_range("Health steps", daily_steps, "health_steps", coverage, start, end)
        self.health_summary = _load_date_range("Health summary", daily_health_summary, "health", coverage, start, end)
        self.hr = _load_date_range("Heart rate", heart_rate_measurements, "heart_rate", coverage, start, end)
        self.stress = _load_date_range("Stress", daily_stress, "stress", coverage, start, end)
        self.calories = _load_date_range("Calories", calorie_burns, "calories", coverage, start, end)
        self.naps = _load_date_range("Naps", nap_sessions, "naps", coverage, start, end)
        self.activity_summary = _load_date_range("Activity summary", activity_summaries, "activity_summary", coverage, start, end)
        self.movement = _load_date_range("Movement", movement_records, "movement", coverage, start, end)
        self.ecg = _load_date_range("ECG", ecg_measurements, "ecg", coverage, start, end)
        # Web
        self.browsing = _load_date_range("Web browsing", daily_browsing, "web", coverage, start, end)
        # Social
        self.messenger = _load_date_range("Messenger", daily_messenger_activity, "messenger", coverage, start, end)
        self.raindrop = _load_date_range("Raindrop", daily_raindrop_activity, "raindrop", coverage, start, end)
        # Substance
        self.substance = _load("Substance", substance_entries)
        # Prompt-facing capture streams
        self.clipboard = _load_date_range("Clipboard", clipboard_entries, "clipboard", coverage, start, end)
        self.irc = _load_date_range("IRC", irc_conversations, "irc", coverage, start, end)
        self.raw_log = _load_date_range("Raw log", raw_log_entries, "raw_log", coverage, start, end)

        # Build per-day indexes
        self._index_by_day()
        self._segments_by_date: dict[date, Any] = {}
        self.work_sessions = _work_sessions_from_batch(self)
        self._work_sessions_by_date = _group_by_date(self.work_sessions, lambda s: s.start.date() if hasattr(s, 'start') and s.start else None)
        print(f"    → Batch load complete\n")

    def _index_by_day(self):
        """Build date→list indexes for fast per-day slicing."""
        self._frag_by_date = {f.date: f for f in self.fragmentation}
        self._attn_by_date = {a.date: a for a in self.attention}
        self._circ_by_date = _group_by_date(self.circadian, lambda c: c.date)
        self._git_daily_by_date = _group_by_date(self.git_daily, lambda g: g.date)
        self._git_facts_by_date = _group_by_date(self.git_facts, lambda f: f.authored_at.date() if hasattr(f, 'authored_at') and f.authored_at else None)
        self._git_sessions_by_date = _group_by_date(self.git_sessions, lambda s: s.start.date() if hasattr(s, 'start') and s.start else None)
        self._github_items_by_repo_number: dict[tuple[str, str, int], dict[str, Any]] = {}
        if isinstance(getattr(self, "github_context", None), dict):
            for item in self.github_context.get("items", []) or []:
                if isinstance(item, dict) and item.get("repo") and item.get("kind") and item.get("number"):
                    self._github_items_by_repo_number[(str(item["repo"]), str(item["kind"]), int(item["number"]))] = item
        self._focus_by_date = _group_by_date(self.focus_spans, lambda s: s.start.date() if hasattr(s, 'start') and s.start else None)
        self._dw_by_date = _group_by_date(self.deep_work, lambda b: b.start.date() if hasattr(b, 'start') and b.start else None)
        self._sf_by_date = _group_by_date(self.sustained_focus, lambda b: b.start.date() if hasattr(b, 'start') and b.start else None)
        self._poly_events_by_date = _group_by_date(self.poly_events, lambda e: e.start.date() if hasattr(e, 'start') and e.start else None)
        self._poly_summaries_by_date = _group_by_date(self.poly_summaries, lambda s: s.date if hasattr(s, 'date') else None)
        self._poly_sessions_by_date = _group_by_date(
            self.poly_sessions,
            lambda s: getattr(s, "canonical_session_date", None)
            or (s.last_message_at.date() if getattr(s, "last_message_at", None) else None)
            or (s.first_message_at.date() if getattr(s, "first_message_at", None) else None),
        )
        self._poly_transcripts_by_date = _group_by_date(
            self.poly_transcripts,
            lambda t: getattr(t, "canonical_session_date", None)
            or (t.last_message_at.date() if getattr(t, "last_message_at", None) else None)
            or (t.first_message_at.date() if getattr(t, "first_message_at", None) else None),
        )
        self._shells_by_date = _group_by_date(self.shell_sessions, lambda s: s.start.date() if hasattr(s, 'start') and s.start else None)
        self._sleep_by_date = _group_by_date(self.sleep, lambda s: s.date if hasattr(s, 'date') else None)
        self._sleep_architecture_by_date = _group_by_date(self.sleep_architecture, lambda s: s.date if hasattr(s, 'date') else None)
        self._steps_by_date = {s.date: s for s in self.steps}
        self._health_by_date = {h.date: h for h in self.health_summary}
        self._hr_by_date = _group_by_date(self.hr, lambda h: h.timestamp.date() if hasattr(h, 'timestamp') and h.timestamp else None)
        self._stress_by_date = {s.date: s for s in self.stress}
        self._browsing_by_date = {b.date: b for b in self.browsing}
        self._messenger_by_date = {m.date: m for m in self.messenger}
        self._raindrop_by_date = {r.date: r for r in self.raindrop}
        self._substance_by_date: dict[date, list] = defaultdict(list)
        for e in self.substance:
            self._substance_by_date[e.date].append(e)
        self._clipboard_by_date = _group_by_date(self.clipboard, lambda c: c.date if hasattr(c, 'date') else None)
        self._irc_by_date = _group_by_date(self.irc, lambda c: c.start.date() if hasattr(c, 'start') and c.start else None)
        self._raw_log_by_date = _group_by_date(self.raw_log, lambda r: r.date if hasattr(r, 'date') else None)
        self._sleep_stages_by_date = _group_by_date(self.sleep_stages, lambda s: s.start.date() if hasattr(s, 'start') and s.start else None)
        self._calories_by_date = {c.date: c for c in self.calories}
        self._naps_by_date = _group_by_date(self.naps, lambda n: n.start.date() if hasattr(n, 'start') and n.start else None)
        self._activity_summary_by_date = {a.date: a for a in self.activity_summary}
        self._movement_by_date = _group_by_date(self.movement, lambda m: m.start.date() if hasattr(m, 'start') and m.start else None)
        self._ecg_by_date = _group_by_date(self.ecg, lambda e: e.start.date() if hasattr(e, 'start') and e.start else None)


def _project_from_path(path: str) -> str | None:
    marker = "/realm/project/"
    if marker in path:
        tail = path.split(marker, 1)[1]
        project = tail.split("/", 1)[0]
        return project or None
    return None


def _dt_key(dt: datetime) -> float:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).timestamp()
    return dt.timestamp()


def _minutes_between(start: datetime, end: datetime) -> float:
    return max((_dt_key(end) - _dt_key(start)) / 60, 0.0)


def _work_sessions_from_batch(batch: BatchSources, min_duration_min: float = 10) -> list:
    """Reconstruct work sessions from already loaded AW/git/shell/Polylogue data."""
    from ..sources.timeline import TimelineEvent, WorkSession

    events: list[TimelineEvent] = []
    for sess in batch.app_sessions:
        if not getattr(sess, "project", None):
            continue
        parts = []
        if sess.mode:
            parts.append(str(sess.mode).capitalize())
        if sess.project:
            parts.append(str(sess.project))
        if sess.app:
            parts.append(f"in {sess.app}")
        events.append(TimelineEvent(
            start=sess.start,
            end=sess.end,
            source="aw",
            kind="focus",
            summary=" ".join(parts) if parts else "Active",
            project=sess.project,
            mode=sess.mode,
        ))

    for fact in batch.git_facts:
        if not getattr(fact, "authored_at", None):
            continue
        events.append(TimelineEvent(
            start=fact.authored_at,
            end=fact.authored_at + timedelta(seconds=1),
            source="git",
            kind="commit",
            summary=f"commit: {fact.subject[:80]}" if getattr(fact, "subject", None) else "commit",
            project=fact.repo,
            mode="coding",
        ))

    for sess in batch.shell_sessions:
        if not getattr(sess, "project", None):
            continue
        parts = [f"{sess.command_count} commands"]
        if sess.project:
            parts.append(f"in {sess.project}")
        if sess.category:
            parts.append(f"({sess.category})")
        events.append(TimelineEvent(
            start=sess.start,
            end=sess.end,
            source="terminal",
            kind="session",
            summary=" ".join(parts),
            project=sess.project,
            mode="shell",
        ))

    for ev in batch.poly_events:
        if not getattr(ev, "start", None):
            continue
        project = None
        for path in getattr(ev, "file_paths", ()) or ():
            project = _project_from_path(str(path))
            if project:
                break
        if not project:
            continue
        events.append(TimelineEvent(
            start=ev.start,
            end=ev.end or ev.start + timedelta(minutes=5),
            source="chat",
            kind=ev.kind,
            summary=ev.summary or f"{ev.provider} {ev.kind}",
            project=project,
            mode="chat",
        ))

    by_project: dict[str, list[TimelineEvent]] = defaultdict(list)
    for event in events:
        if event.project:
            by_project[event.project].append(event)

    result = []
    for project, project_events in by_project.items():
        project_events.sort(key=lambda e: _dt_key(e.start))
        session_events: list[TimelineEvent] = []
        for event in project_events:
            if not session_events:
                session_events = [event]
                continue
            gap_min = _minutes_between(session_events[-1].end, event.start)
            if gap_min <= 30:
                session_events.append(event)
            else:
                _add_batch_work_session(result, WorkSession, project, session_events, min_duration_min)
                session_events = [event]
        _add_batch_work_session(result, WorkSession, project, session_events, min_duration_min)

    result.sort(key=lambda s: _dt_key(s.start))
    return result


def _add_batch_work_session(result: list, cls, project: str, events: list, min_duration_min: float) -> None:
    if not events:
        return
    start = min((e.start for e in events), key=_dt_key)
    end = max((e.end for e in events), key=_dt_key)
    duration_min = _minutes_between(start, end)
    if duration_min < min_duration_min:
        return
    breakdown: dict[str, int] = {}
    for event in events:
        breakdown[event.source] = breakdown.get(event.source, 0) + 1
    result.append(cls(
        project=project,
        start=start,
        end=end,
        duration_min=round(duration_min, 1),
        events=tuple(events),
        source_breakdown=breakdown,
    ))


def _group_by_date(items: list, date_fn) -> dict[date, list]:
    """Group items by date using a date extraction function."""
    result: dict[date, list] = defaultdict(list)
    for item in items:
        d = date_fn(item)
        if d is not None:
            result[d].append(item)
    return result


def _segments_from_batch(batch: BatchSources, start: date, end: date) -> list:
    return [
        seg for day, seg in sorted(batch._segments_by_date.items())
        if start <= day <= end and seg is not None
    ]


def _day_summary_from_batch(
    d: date,
    *,
    seg,
    active_h: float,
    git_act: list,
    facts: list,
    poly_events: list,
    poly_summaries: list,
    shells: list,
    sleep_data: list,
    health_summary: list,
    naps_day: list,
):
    """Build the two-track day summary from already loaded day slices."""
    from ..sources.day_summary import DaySummary, HumanSegment, AIBlock, OverlapInsight

    human_segments = ()
    if seg:
        human_segments = tuple(
            HumanSegment(
                start=s.start,
                end=s.end,
                duration_min=s.duration_min,
                context=s.context,
                projects=s.projects,
            )
            for s in seg.segments
        )

    raw_blocks: list[dict[str, Any]] = []
    for ev in sorted(poly_events, key=lambda e: _dt_key(e.start) if getattr(e, "start", None) else float("-inf")):
        if ev.start is None:
            continue
        ev_s = ev.start
        ev_e = ev.end if ev.end else ev_s + timedelta(minutes=5)
        if raw_blocks and (ev_s - raw_blocks[-1]["end"]).total_seconds() < 600:
            raw_blocks[-1]["end"] = max(raw_blocks[-1]["end"], ev_e)
            raw_blocks[-1]["kinds"].add(ev.kind)
            raw_blocks[-1]["files"] += len(ev.file_paths)
        else:
            raw_blocks.append({
                "start": ev_s,
                "end": ev_e,
                "kinds": {ev.kind},
                "files": len(ev.file_paths),
            })

    ai_blocks: list[AIBlock] = []
    for block in raw_blocks:
        commits = sum(
            1 for f in facts
            if _dt_key(f.authored_at) >= _dt_key(block["start"]) and _dt_key(f.authored_at) < _dt_key(block["end"])
        )
        duration_min = (block["end"] - block["start"]).total_seconds() / 60
        ai_blocks.append(AIBlock(
            start=block["start"],
            end=block["end"],
            duration_min=round(duration_min, 1),
            kinds=tuple(sorted(block["kinds"])),
            file_count=block["files"],
            commit_count=commits,
        ))

    overlaps: list[OverlapInsight] = []
    if seg:
        for block in ai_blocks:
            if block.commit_count == 0:
                continue
            human_contexts: list[str] = []
            for s in seg.segments:
                if _dt_key(s.end) > _dt_key(block.start) and _dt_key(s.start) < _dt_key(block.end):
                    if s.context not in human_contexts:
                        human_contexts.append(s.context)
            overlaps.append(OverlapInsight(
                ai_start=block.start,
                ai_end=block.end,
                ai_kinds=block.kinds,
                ai_commits=block.commit_count,
                human_contexts=tuple(human_contexts),
            ))

    total_commits = sum(g.commit_count for g in git_act)
    repos = tuple(sorted(set(g.repo.split("/")[-1] for g in git_act)))
    lines_added = sum(g.lines_added for g in git_act)
    lines_deleted = sum(g.lines_deleted for g in git_act)
    ai_sessions = sum(s.session_count for s in poly_summaries)
    ai_messages = sum(s.total_messages for s in poly_summaries)
    shell_cmds = sum(s.command_count for s in shells)
    shell_errs = sum(s.error_count for s in shells)

    sleep_hours = None
    sleep_score = None
    sleep_stages = None
    if sleep_data:
        best = max(sleep_data, key=lambda s: getattr(s, "bed_duration_min", 0) or 0)
        sleep_hours = round(best.bed_duration_min / 60, 1)
        sleep_score = best.sleep_score
        sleep_stages = best.sleep_stages

    health = health_summary[0] if health_summary else None
    nap_minutes = round(sum(n.duration_min for n in naps_day), 1) if naps_day else 0.0

    return DaySummary(
        date=d,
        human_segments=human_segments,
        ai_blocks=tuple(ai_blocks),
        overlaps=tuple(overlaps),
        active_hours=round(active_h, 1),
        commit_count=total_commits,
        commit_repos=repos,
        lines_added=lines_added,
        lines_deleted=lines_deleted,
        ai_session_count=ai_sessions,
        ai_message_count=ai_messages,
        shell_commands=shell_cmds,
        shell_error_rate=round(shell_errs / max(shell_cmds, 1), 3),
        sleep_hours=sleep_hours,
        sleep_score=sleep_score,
        sleep_stages=sleep_stages,
        stress_avg=round(health.stress_avg, 1) if health and health.stress_avg is not None else None,
        heart_rate_avg=round(health.heart_rate_avg, 1) if health and health.heart_rate_avg is not None else None,
        hrv_rmssd=round(health.hrv_rmssd_avg, 2) if health and health.hrv_rmssd_avg is not None else None,
        spo2_avg=round(health.spo2_avg, 1) if health and health.spo2_avg is not None else None,
        respiratory_avg=round(health.respiratory_avg, 1) if health and health.respiratory_avg is not None else None,
        calories=round(health.calories, 1) if health and health.calories is not None else None,
        nap_minutes=nap_minutes,
    )


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
    s_dt, e_dt = date_to_dt_range(d, d)

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
        github_context = _filter_github_context_for_facts(getattr(batch, "github_context", None), facts)
        poly_events = batch._poly_events_by_date.get(d, [])
        poly_summaries = batch._poly_summaries_by_date.get(d, [])
        shells = batch._shells_by_date.get(d, [])
        sleep_data = batch._sleep_by_date.get(d, []) + batch._sleep_by_date.get(d - timedelta(days=1), [])
        steps = [batch._steps_by_date[d]] if d in batch._steps_by_date else []
        health_summary = [batch._health_by_date[d]] if d in batch._health_by_date else []
        hr_measurements = batch._hr_by_date.get(d, [])
        stress_measurements = [batch._stress_by_date[d]] if d in batch._stress_by_date else []
        browsing = [batch._browsing_by_date[d]] if d in batch._browsing_by_date else []
        messenger = [batch._messenger_by_date[d]] if d in batch._messenger_by_date else []
        raindrop = [batch._raindrop_by_date[d]] if d in batch._raindrop_by_date else []
        work_sess = batch._work_sessions_by_date.get(d, [])
        substance_day = batch._substance_by_date.get(d, [])
        clipboard_day = getattr(batch, "_clipboard_by_date", {}).get(d, [])
        irc_day = getattr(batch, "_irc_by_date", {}).get(d, [])
        raw_log_day = getattr(batch, "_raw_log_by_date", {}).get(d, [])
        sleep_stages_day = batch._sleep_stages_by_date.get(d, [])
        sleep_architecture_day = batch._sleep_architecture_by_date.get(d, []) + batch._sleep_architecture_by_date.get(d - timedelta(days=1), [])
        calories_day = [batch._calories_by_date[d]] if d in batch._calories_by_date else []
        naps_day = batch._naps_by_date.get(d, [])
        activity_summary_day = [batch._activity_summary_by_date[d]] if d in batch._activity_summary_by_date else []
        movement_day = batch._movement_by_date.get(d, [])
        ecg_day = batch._ecg_by_date.get(d, [])
        poly_sessions = batch._poly_sessions_by_date.get(d, [])
        poly_transcripts = batch._poly_transcripts_by_date.get(d, [])
        # Per-day AW segmentation is only useful on days with AW activity.
        from ..sources.activity_segments import segment_day
        if active_h > 0:
            seg = batch._segments_by_date.get(d)
            if seg is None:
                seg = _safe(segment_day, d, default=None)
                if seg is not None:
                    batch._segments_by_date[d] = seg
        else:
            seg = None
        two_track = _day_summary_from_batch(
            d,
            seg=seg,
            active_h=active_h,
            git_act=git_act,
            facts=facts,
            poly_events=poly_events,
            poly_summaries=poly_summaries,
            shells=shells,
            sleep_data=sleep_data,
            health_summary=health_summary,
            naps_day=naps_day,
        )
    else:
        # Slow path: query each source individually
        from ..sources.activitywatch import (
            active_seconds_by_date, focus_spans, app_sessions,
            deep_work, fragmentation, attention, circadian,
            sustained_focus, daily_activity as aw_daily,
        )
        from ..sources.activity_segments import segment_day
        from ..sources.git import commit_facts, daily_activity as git_daily, commit_sessions, github_context_for_commits
        from ..sources.polylogue import work_events, day_session_summaries
        from ..sources.terminal import shell_sessions
        from ..sources.sleep_infer import infer_sleep
        from ..sources.sleep import sleep_stages as sleep_stages_fn, sleep_architecture as sleep_architecture_fn
        from ..sources.health import (
            daily_steps, daily_health_summary, heart_rate_measurements, daily_stress,
            calorie_burns, nap_sessions, activity_summaries, movement_records,
            ecg_measurements,
        )
        from ..sources.web import daily_browsing
        from ..sources.exports import daily_messenger_activity, daily_raindrop_activity
        from ..sources.timeline import work_sessions as ws_fn
        from ..sources.day_summary import day_summary
        from ..sources.substance import entries_for_date as substance_for_date
        from ..sources.clipboard import entries_in_range as clipboard_entries
        from ..sources.irc import conversations_in_range as irc_conversations
        from ..sources.raw_log import entries_in_range as raw_log_entries

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
        github_context = _safe(github_context_for_commits, facts, default={"status": "unavailable", "items": []})
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
        clipboard_day = _safe(clipboard_entries, start=d, end=d, default=[])
        irc_day = _safe(irc_conversations, start=d, end=d, default=[])
        raw_log_day = _safe(raw_log_entries, start=d, end=d, default=[])
        sleep_stages_day = _safe(sleep_stages_fn, start=d - timedelta(days=1), end=d, default=[])
        sleep_architecture_day = _safe(sleep_architecture_fn, start=d - timedelta(days=1), end=d, default=[])
        calories_day = _safe(calorie_burns, start=d, end=d, default=[])
        naps_day = _safe(nap_sessions, start=d, end=d, default=[])
        activity_summary_day = _safe(activity_summaries, start=d, end=d, default=[])
        movement_day = _safe(movement_records, start=d, end=d, default=[])
        ecg_day = _safe(ecg_measurements, start=d, end=d, default=[])
        seg = _safe(segment_day, d, default=None)
        two_track = _safe(day_summary, d, default=None)
        poly_sessions = None
        poly_transcripts = None

    from ..sources.activitywatch import focus_timeline as focus_timeline_fn
    from ..sources.polylogue import session_profiles_for_date, conversation_transcripts

    focus_rows = _safe(focus_timeline_fn, start=s_dt, end=e_dt, default=[])
    if poly_sessions is None:
        poly_sessions = _safe(session_profiles_for_date, start=d, end=d, default=[])
    if poly_transcripts is None:
        poly_transcripts = _safe(conversation_transcripts, start=d, end=d, default=[])
    focus_payload = _build_focus_timeline_payload(d, focus_rows)
    ai_payload = _build_ai_activity_payload(
        poly_events=poly_events,
        poly_summaries=poly_summaries,
        sessions=poly_sessions,
        transcripts=poly_transcripts,
    )
    sleep_payload = _build_sleep_payload(sleep_data, sleep_architecture_day)

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
        "focus_rows": len(focus_rows) if focus_rows else None,
    }

    # ── Baseline comparison ──
    baseline_metrics = {
        "active_hours": active_h if active_h else None,
        "commit_count": total_commits if total_commits else None,
        "fragmentation": frag[0].fragmentation if frag else None,
    }
    baseline = _safe(_baseline_comparison, d, baseline_metrics, all_features, default={})
    narrative_brief = _build_day_narrative_brief(
        d,
        active_h=active_h,
        seg=seg,
        git_act=git_act,
        facts=facts,
        poly_events=poly_events,
        poly_summaries=poly_summaries,
        poly_sessions=poly_sessions,
        poly_transcripts=poly_transcripts,
        shells=shells,
        sleep_data=sleep_data,
        work_sess=work_sess,
        clipboard_day=clipboard_day,
        irc_day=irc_day,
        raw_log_day=raw_log_day,
        baseline=baseline,
        two_track=two_track,
    )

    # ── Write files ──
    day_dir.mkdir(parents=True, exist_ok=True)

    write_json(day_dir / "metrics.json", metrics)
    write_json(day_dir / "focus_timeline.json", focus_payload)
    if segments_data:
        write_json(day_dir / "segments.json", segments_data)
    write_json(
        day_dir / "commits.json",
        {
            **_build_commit_payload(facts=facts, sessions=sessions, daily=git_act),
            "github": github_context,
        },
    )
    write_json(day_dir / "ai_activity.json", ai_payload)
    write_json(day_dir / "shell.json", shells)
    write_json(day_dir / "sleep.json", sleep_payload)
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
        "ecg": ecg_day,
    })
    if browsing:
        write_json(day_dir / "browsing.json", browsing)
    if messenger or raindrop:
        write_json(day_dir / "social.json", {"messenger": messenger, "raindrop": raindrop})
    if work_sess:
        write_json(day_dir / "work_sessions.json", work_sess)
    if substance_day:
        write_json(day_dir / "substance.json", substance_day)
    if clipboard_day:
        write_json(day_dir / "clipboard.json", _build_clipboard_payload(clipboard_day))
    if irc_day:
        write_json(day_dir / "irc.json", _build_irc_payload(irc_day))
    if raw_log_day:
        write_json(day_dir / "raw_log.json", _build_raw_log_payload(raw_log_day))
    if two_track:
        write_json(day_dir / "two_track.json", two_track)
    write_json(day_dir / "baseline.json", baseline)
    write_json(day_dir / "narrative_brief.json", narrative_brief)

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
        sleep_arch = [sl for sl in batch.sleep_architecture if hasattr(sl, 'date') and s <= sl.date <= e]
        health = [h for h in batch.health_summary if s <= h.date <= e]
        browsing = [b for b in batch.browsing if s <= b.date <= e]
        features = [f for f in all_features if s <= f.date <= e]
    else:
        # Slow path
        from ..sources.activitywatch import active_seconds_by_date
        from ..sources.git import daily_activity as git_daily, commit_facts
        from ..sources.polylogue import day_session_summaries, work_events
        from ..sources.sleep import entries_in_range as sleep_range, sleep_architecture
        from ..sources.health import daily_health_summary
        from ..sources.web import daily_browsing
        from ..sources.patterns import build_day_features
        active_map = _safe(active_seconds_by_date, s, e, default={})
        git_act = _safe(git_daily, start=s, end=e, default=[])
        git_facts = _safe(commit_facts, start=s, end=e, default=[])
        poly_summaries = _safe(day_session_summaries, start=s, end=e, default=[])
        poly_events = _safe(work_events, start=s, end=e, default=[])
        sleep = _safe(sleep_range, s, e, default=[])
        sleep_arch = _safe(sleep_architecture, start=s, end=e, default=[])
        health = _safe(daily_health_summary, start=s, end=e, default=[])
        browsing = _safe(daily_browsing, start=s, end=e, default=[])
        features = _safe(build_day_features, s, e, default=[])

    from ..sources.patterns import weekly_rhythm, activity_trends
    rhythm = _safe(weekly_rhythm, features, default=None) if features else None
    trends = _safe(activity_trends, features, default={}) if features else {}

    if batch:
        from ..sources.activity_segments import transition_bigrams
        hourly = [c for c in batch.circadian if s <= c.date <= e]
        transitions = _safe(transition_bigrams, _segments_from_batch(batch, s, e), default=None)
    else:
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

    # Browsing aggregation for week
    browsing_week = {}
    if browsing:
        browsing_week["total_visits"] = sum(b.visit_count for b in browsing)
        all_domains = set()
        for b in browsing:
            all_domains.update(d for d, _ in b.top_domains)
        browsing_week["unique_domains"] = len(all_domains)

    week_sleep_summary = _summarize_sleep(sleep, sleep_arch)
    week_health_summary = _summarize_health(health)
    week_ai_summary = _summarize_ai(poly_summaries, poly_events)
    week_narrative_brief = _build_week_narrative_brief(
        week_key,
        start=s,
        end=e,
        day_metrics=day_metrics,
        total_commits=sum(g.commit_count for g in git_act) if git_act else 0,
        project_commits=project_commits,
        kind_dist=kind_dist,
        rhythm=rhythm,
        trends=trends,
        sleep_summary=week_sleep_summary,
        ai_summary=week_ai_summary,
    )

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
        "sleep": week_sleep_summary,
        "health": week_health_summary,
        "ai": week_ai_summary,
        "browsing": browsing_week,
    })
    write_json(week_dir / "week_transitions.json", transitions)
    write_json(week_dir / "week_intraday.json", hourly)
    if rhythm:
        write_json(week_dir / "week_rhythm.json", rhythm)
    if trends:
        write_json(week_dir / "week_trends.json", trends)
    write_json(week_dir / "narrative_brief.json", week_narrative_brief)

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
        sleep_arch = [sl for sl in batch.sleep_architecture if hasattr(sl, 'date') and s <= sl.date <= e]
        steps = [st for st in batch.steps if s <= st.date <= e]
        health = [h for h in batch.health_summary if s <= h.date <= e]
        browsing = [b for b in batch.browsing if s <= b.date <= e]
        features = [f for f in all_features if s <= f.date <= e]
    else:
        from ..sources.activitywatch import active_seconds_by_date
        from ..sources.git import daily_activity as git_daily
        from ..sources.polylogue import day_session_summaries, work_events
        from ..sources.sleep import entries_in_range as sleep_range, sleep_architecture
        from ..sources.health import daily_steps, daily_health_summary
        from ..sources.web import daily_browsing
        from ..sources.patterns import build_day_features
        active_map = _safe(active_seconds_by_date, s, e, default={})
        git_act = _safe(git_daily, start=s, end=e, default=[])
        poly_summaries = _safe(day_session_summaries, start=s, end=e, default=[])
        poly_events = _safe(work_events, start=s, end=e, default=[])
        sleep = _safe(sleep_range, s, e, default=[])
        sleep_arch = _safe(sleep_architecture, start=s, end=e, default=[])
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

    if batch:
        from ..sources.activity_segments import transition_bigrams
        transitions = _safe(transition_bigrams, _segments_from_batch(batch, s, e), default=None)
    else:
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

    # Browsing aggregation for month
    browsing_month = {}
    if browsing:
        browsing_month["total_visits"] = sum(b.visit_count for b in browsing)
        all_domains = set()
        for b in browsing:
            all_domains.update(d for d, _ in b.top_domains)
        browsing_month["unique_domains"] = len(all_domains)

    month_sleep_summary = _summarize_sleep(sleep, sleep_arch)
    month_health_summary = _summarize_health(health)
    month_ai_summary = _summarize_ai(poly_summaries, poly_events)
    month_narrative_brief = _build_month_narrative_brief(
        month_key,
        start=s,
        end=e,
        day_metrics=day_metrics,
        per_week_commits=per_week_commits,
        project_by_week={wk: dict(c) for wk, c in project_by_week.items()},
        rhythm=analysis.rhythm if analysis else None,
        drivers=analysis.drivers if analysis else [],
        anomalies=analysis.anomalies if analysis else {},
        regime_changes=analysis.regime_changes if analysis else [],
        trends=analysis.trends if analysis else {},
        sleep_summary=month_sleep_summary,
        ai_summary=month_ai_summary,
    )

    write_json(month_dir / "month_metrics.json", {
        "month": month_key,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "per_day": day_metrics,
        "per_week_commits": per_week_commits,
        "project_by_week": {wk: dict(c.most_common()) for wk, c in project_by_week.items()},
        "total_active_hours": round(sum(v / 3600 for v in (active_map or {}).values()), 2),
        "total_commits": sum(g.commit_count for g in git_act) if git_act else 0,
        "sleep": month_sleep_summary,
        "health": month_health_summary,
        "ai": month_ai_summary,
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
    write_json(month_dir / "narrative_brief.json", month_narrative_brief)

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
        poly_summaries = [p for p in batch.poly_summaries if hasattr(p, 'date') and s <= p.date <= e]
        poly_events = [p for p in batch.poly_events if hasattr(p, 'start') and p.start and s <= p.start.date() <= e]
        sleep = [sl for sl in batch.sleep if hasattr(sl, 'date') and s <= sl.date <= e]
        sleep_arch = [sl for sl in batch.sleep_architecture if hasattr(sl, 'date') and s <= sl.date <= e]
        health = [h for h in batch.health_summary if s <= h.date <= e]
    else:
        from ..sources.patterns import build_day_features
        from ..sources.git import daily_activity as git_daily
        from ..sources.activitywatch import active_seconds_by_date
        from ..sources.polylogue import day_session_summaries, work_events
        from ..sources.sleep import entries_in_range as sleep_range, sleep_architecture
        from ..sources.health import daily_health_summary
        features = _safe(build_day_features, s, e, default=[])
        git_act = _safe(git_daily, start=s, end=e, default=[])
        active_map = _safe(active_seconds_by_date, s, e, default={})
        poly_summaries = _safe(day_session_summaries, start=s, end=e, default=[])
        poly_events = _safe(work_events, start=s, end=e, default=[])
        sleep = _safe(sleep_range, s, e, default=[])
        sleep_arch = _safe(sleep_architecture, start=s, end=e, default=[])
        health = _safe(daily_health_summary, start=s, end=e, default=[])

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

    quarter_sleep_summary = _summarize_sleep(sleep, sleep_arch)
    quarter_health_summary = _summarize_health(health)
    quarter_ai_summary = _summarize_ai(poly_summaries, poly_events)
    quarter_narrative_brief = _build_rollup_narrative_brief(
        "quarter",
        quarter_key,
        start=s,
        end=e,
        per_unit=per_month,
        unit_key="month",
        project_counts=_sum_nested_outer_counts(project_arcs),
        ai_summary=quarter_ai_summary,
        sleep_summary=quarter_sleep_summary,
        trends=trends,
    )

    q_dir.mkdir(parents=True, exist_ok=True)

    write_json(q_dir / "quarter_metrics.json", {
        "quarter": quarter_key,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "per_month": per_month,
        "total_active_hours": round(sum(v / 3600 for v in (active_map or {}).values()), 2),
        "total_commits": sum(g.commit_count for g in git_act) if git_act else 0,
        "sleep": quarter_sleep_summary,
        "health": quarter_health_summary,
        "ai": quarter_ai_summary,
    })
    write_json(q_dir / "quarter_trends.json", trends)
    if regimes:
        write_json(q_dir / "quarter_regimes.json", regimes)
    write_json(q_dir / "quarter_project_arcs.json", {
        repo: dict(months) for repo, months in project_arcs.items()
    })
    write_json(q_dir / "narrative_brief.json", quarter_narrative_brief)

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
        poly_summaries = [p for p in batch.poly_summaries if hasattr(p, 'date') and s <= p.date <= e]
        poly_events = [p for p in batch.poly_events if hasattr(p, 'start') and p.start and s <= p.start.date() <= e]
        sleep = [sl for sl in batch.sleep if hasattr(sl, 'date') and s <= sl.date <= e]
        sleep_arch = [sl for sl in batch.sleep_architecture if hasattr(sl, 'date') and s <= sl.date <= e]
        health = [h for h in batch.health_summary if s <= h.date <= e]
    else:
        from ..sources.patterns import build_day_features
        from ..sources.git import daily_activity as git_daily
        from ..sources.activitywatch import active_seconds_by_date
        from ..sources.polylogue import day_session_summaries, work_events
        from ..sources.sleep import entries_in_range as sleep_range, sleep_architecture
        from ..sources.health import daily_health_summary
        features = _safe(build_day_features, s, e, default=[])
        git_act = _safe(git_daily, start=s, end=e, default=[])
        active_map = _safe(active_seconds_by_date, s, e, default={})
        poly_summaries = _safe(day_session_summaries, start=s, end=e, default=[])
        poly_events = _safe(work_events, start=s, end=e, default=[])
        sleep = _safe(sleep_range, s, e, default=[])
        sleep_arch = _safe(sleep_architecture, start=s, end=e, default=[])
        health = _safe(daily_health_summary, start=s, end=e, default=[])

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

    half_project_counts: Counter = Counter()
    for g in (git_act or []):
        half_project_counts[g.repo] += g.commit_count
    half_sleep_summary = _summarize_sleep(sleep, sleep_arch)
    half_health_summary = _summarize_health(health)
    half_ai_summary = _summarize_ai(poly_summaries, poly_events)
    half_narrative_brief = _build_rollup_narrative_brief(
        "half",
        half_key,
        start=s,
        end=e,
        per_unit=per_quarter,
        unit_key="quarter",
        project_counts=half_project_counts,
        ai_summary=half_ai_summary,
        sleep_summary=half_sleep_summary,
        trends=trends,
    )

    h_dir.mkdir(parents=True, exist_ok=True)

    write_json(h_dir / "half_metrics.json", {
        "half": half_key,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "per_quarter": per_quarter,
        "total_active_hours": round(sum(v / 3600 for v in (active_map or {}).values()), 2),
        "total_commits": sum(g.commit_count for g in git_act) if git_act else 0,
        "sleep": half_sleep_summary,
        "health": half_health_summary,
        "ai": half_ai_summary,
    })
    if trends:
        write_json(h_dir / "half_trends.json", trends)
    write_json(h_dir / "narrative_brief.json", half_narrative_brief)

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
        poly_events = [p for p in batch.poly_events if hasattr(p, 'start') and p.start and s <= p.start.date() <= e]
        sleep = [sl for sl in batch.sleep if hasattr(sl, 'date') and s <= sl.date <= e]
        sleep_arch = [sl for sl in batch.sleep_architecture if hasattr(sl, 'date') and s <= sl.date <= e]
        health = [h for h in batch.health_summary if s <= h.date <= e]
    else:
        from ..sources.patterns import build_day_features
        from ..sources.git import daily_activity as git_daily
        from ..sources.activitywatch import active_seconds_by_date
        from ..sources.polylogue import day_session_summaries, work_events
        from ..sources.sleep import entries_in_range as sleep_range, sleep_architecture
        from ..sources.health import daily_health_summary
        features = _safe(build_day_features, s, e, default=[])
        git_act = _safe(git_daily, start=s, end=e, default=[])
        active_map = _safe(active_seconds_by_date, s, e, default={})
        chat_act = _safe(day_session_summaries, start=s, end=e, default=[])
        poly_events = _safe(work_events, start=s, end=e, default=[])
        sleep = _safe(sleep_range, s, e, default=[])
        sleep_arch = _safe(sleep_architecture, start=s, end=e, default=[])
        health = _safe(daily_health_summary, start=s, end=e, default=[])

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
        providers = getattr(c, "providers", None)
        if providers:
            for provider, count in providers.items():
                provider_months[provider][mk] += count
        else:
            provider_months[getattr(c, 'provider', 'unknown')][mk] += getattr(c, 'session_count', 0)

    year_sleep_summary = _summarize_sleep(sleep, sleep_arch)
    year_health_summary = _summarize_health(health)
    year_ai_summary = _summarize_ai(chat_act, poly_events)
    year_narrative_brief = _build_rollup_narrative_brief(
        "year",
        year_key,
        start=s,
        end=e,
        per_unit=per_month,
        unit_key="month",
        project_counts=_sum_nested_outer_counts(project_arcs),
        ai_summary=year_ai_summary,
        sleep_summary=year_sleep_summary,
        trends=trends,
    )

    y_dir.mkdir(parents=True, exist_ok=True)

    write_json(y_dir / "year_metrics.json", {
        "year": year_key,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "per_month": per_month,
        "total_active_hours": round(sum(v / 3600 for v in (active_map or {}).values()), 2),
        "total_commits": sum(g.commit_count for g in git_act) if git_act else 0,
        "sleep": year_sleep_summary,
        "health": year_health_summary,
        "ai": year_ai_summary,
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
    write_json(y_dir / "narrative_brief.json", year_narrative_brief)

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

def generate_overview(
    output: Path,
    *,
    force: bool = False,
    data_start: date | None = None,
    data_end: date | None = None,
    all_features: list | None = None,
    batch: BatchSources | None = None,
) -> bool:
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
    features = all_features if all_features is not None else _safe(build_day_features, s, e, default=[])

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
    git_act = [g for g in batch.git_daily if s <= g.date <= e] if batch else _safe(git_daily, start=s, end=e, default=[])
    project_arcs: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for g in (git_act or []):
        mk = key_for_date("month", g.date)
        project_arcs[g.repo][mk] += g.commit_count

    # AI evolution
    chat_act = [p for p in batch.poly_summaries if s <= p.date <= e] if batch else _safe(chat_daily, start=s, end=e, default=[])
    provider_months: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for c in (chat_act or []):
        mk = key_for_date("month", c.date)
        providers = getattr(c, "providers", None)
        if providers:
            for provider, count in providers.items():
                provider_months[provider][mk] += count
        else:
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
    health_all = [h for h in batch.health_summary if s <= h.date <= e] if batch else _safe(daily_health_summary, start=s, end=e, default=[])
    health_patterns = {}
    if health_all:
        health_by_month: dict[str, list] = defaultdict(list)
        for h in health_all:
            mk = h.date.strftime("%Y-%m")
            health_by_month[mk].append(h)
        for mk, entries in sorted(health_by_month.items()):
            health_patterns[mk] = _summarize_health(entries)

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
    global_transitions = None
    if batch:
        from ..sources.activity_segments import transition_bigrams
        global_transitions = _safe(transition_bigrams, _segments_from_batch(batch, s, e), default=None)
    else:
        from ..sources.activity_segments import segment_range, transition_bigrams
        all_segs = _safe(segment_range, start=s, end=e, default=[])
        global_transitions = _safe(transition_bigrams, all_segs, default=None)

    # Source coverage matrix (which sources have data for which months)
    month_keys = period_keys_in_range("month", s, e)
    coverage = _build_source_coverage(features, git_act, chat_act, all_sleep, all_substance, month_keys, health=health_all)
    overview_narrative_brief = _build_overview_narrative_brief(
        start=s,
        end=e,
        source_coverage=coverage,
        project_arcs={repo: dict(months) for repo, months in project_arcs.items()},
        provider_months={prov: dict(months) for prov, months in provider_months.items()},
        trends=trends,
        regime_changes=regime_changes,
        sleep_patterns=sleep_patterns,
    )

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
    write_json(ov_dir / "narrative_brief.json", overview_narrative_brief)
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
        def avg(field: str, ndigits: int):
            vals = [r[field] for r in rows if r.get(field) is not None]
            return round(sum(vals) / len(vals), ndigits) if vals else None

        summaries[sub] = {
            "n_doses": n,
            "avg_active_hours": avg("active_hours", 2),
            "avg_fragmentation": avg("fragmentation", 3),
            "avg_commits": avg("commits", 1),
            "avg_deep_work_min": avg("deep_work_min", 1),
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

@lru_cache(maxsize=1)
def _discover_source_coverage() -> dict[str, DateSpan]:
    """Discover viable date windows so full-history runs avoid empty source queries."""
    today = date.today()
    floor = date(2000, 1, 1)
    coverage: dict[str, DateSpan] = {}

    def add(key: str, span: DateSpan | None, label: str | None = None) -> None:
        if span is None:
            return
        coverage[key] = span
        print(f"    {(label or key) + ':':13s} {_span_text(span)}")

    print("  Scanning sources for data coverage...")

    cfg = get_config()
    add("aw", _sqlite_timestamp_span(
        cfg.activitywatch_db,
        """
        SELECT MIN(e.starttime), MAX(e.starttime)
        FROM events e
        JOIN buckets b ON e.bucketrow = b.id
        WHERE b.type = 'currentwindow'
        """,
    ), "aw")

    add("terminal", _sqlite_timestamp_span(
        cfg.atuin_db,
        "SELECT MIN(timestamp), MAX(timestamp) FROM history",
    ), "terminal")

    from ..sources.sleep import entries as sleep_entries, sleep_stages, sleep_architecture
    sleep = _safe(sleep_entries, default=[])
    add("sleep", _span_from_dates([s.date for s in sleep if getattr(s, "date", None)]), "sleep")
    stages = _safe(sleep_stages, default=[])
    add("sleep_stages", _span_from_dates([s.start.date() for s in stages if getattr(s, "start", None)]), "sleep stages")
    arch = _safe(sleep_architecture, default=[])
    add("sleep_architecture", _span_from_dates([a.date for a in arch if getattr(a, "date", None)]), "sleep arch")

    from ..sources.health import (
        daily_steps, daily_health_summary, heart_rate_measurements, daily_stress,
        calorie_burns, nap_sessions, activity_summaries, movement_records,
        ecg_measurements,
    )
    add("health_steps", _span_from_dates([s.date for s in _safe(daily_steps, default=[]) if getattr(s, "date", None)]), "steps")
    add("health", _span_from_dates([h.date for h in _safe(daily_health_summary, default=[]) if getattr(h, "date", None)]), "health")
    add("heart_rate", _span_from_dates([h.timestamp.date() for h in _safe(heart_rate_measurements, default=[]) if getattr(h, "timestamp", None)]), "heart rate")
    add("stress", _span_from_dates([s.date for s in _safe(daily_stress, default=[]) if getattr(s, "date", None)]), "stress")
    add("calories", _span_from_dates([c.date for c in _safe(calorie_burns, default=[]) if getattr(c, "date", None)]), "calories")
    add("naps", _span_from_dates([n.start.date() for n in _safe(nap_sessions, default=[]) if getattr(n, "start", None)]), "naps")
    add("activity_summary", _span_from_dates([a.date for a in _safe(activity_summaries, default=[]) if getattr(a, "date", None)]), "activity")
    add("movement", _span_from_dates([m.start.date() for m in _safe(movement_records, default=[]) if getattr(m, "start", None)]), "movement")
    add("ecg", _span_from_dates([e.start.date() for e in _safe(ecg_measurements, default=[]) if getattr(e, "start", None)]), "ecg")

    from ..sources.substance import entries as substance_entries
    substance = _safe(substance_entries, default=[])
    add("substance", _span_from_dates([s.date for s in substance if getattr(s, "date", None)]), "substance")

    from ..sources.git import daily_activity as git_daily
    git_rows = _safe(git_daily, start=floor, end=today, default=[])
    add("git", _span_from_dates([g.date for g in git_rows if getattr(g, "date", None)]), "git")

    from ..sources.polylogue import day_session_summaries, work_events
    poly_days = _safe(day_session_summaries, start=floor, end=today, default=[])
    add("polylogue", _span_from_dates([p.date for p in poly_days if getattr(p, "date", None)]), "polylogue")
    poly_events = _safe(work_events, start=floor, end=today, default=[])
    add("polylogue_events", _span_from_dates([p.start.date() for p in poly_events if getattr(p, "start", None)]), "poly events")

    from ..sources.keylog import log_files
    keylog_dates = [p.stem for p in log_files()]
    add("keylog", _span_from_dates([date.fromisoformat(d) for d in keylog_dates]), "keylog")

    from ..sources.spotify import daily_listening
    spotify_rows = _safe(daily_listening, start=floor, end=today, default=[])
    add("spotify", _span_from_dates([s.date for s in spotify_rows if getattr(s, "date", None)]), "spotify")

    from ..sources.reddit import daily_activity as reddit_daily
    reddit_rows = _safe(reddit_daily, start=floor, end=today, default=[])
    add("reddit", _span_from_dates([r.date for r in reddit_rows if getattr(r, "date", None)]), "reddit")

    from ..sources.web import daily_browsing
    web_rows = _safe(daily_browsing, start=floor, end=today, default=[])
    add("web", _span_from_dates([w.date for w in web_rows if getattr(w, "date", None)]), "web")

    from ..sources.exports import daily_messenger_activity, daily_raindrop_activity
    msg_rows = _safe(daily_messenger_activity, start=floor, end=today, default=[])
    add("messenger", _span_from_dates([m.date for m in msg_rows if getattr(m, "date", None)]), "messenger")
    bm_rows = _safe(daily_raindrop_activity, start=floor, end=today, default=[])
    add("raindrop", _span_from_dates([r.date for r in bm_rows if getattr(r, "date", None)]), "raindrop")

    from ..sources.clipboard import entries as clipboard_entries
    clipboard_rows = _safe(lambda: list(clipboard_entries()), default=[])
    add("clipboard", _span_from_dates([c.date for c in clipboard_rows if getattr(c, "date", None)]), "clipboard")

    from ..sources.irc import conversations as irc_conversations
    irc_rows = _safe(lambda: list(irc_conversations()), default=[])
    add("irc", _span_from_dates([c.start.date() for c in irc_rows if getattr(c, "start", None)]), "irc")

    from ..sources.raw_log import entries as raw_log_entries
    raw_log_rows = _safe(lambda: list(raw_log_entries()), default=[])
    add("raw_log", _span_from_dates([r.date for r in raw_log_rows if getattr(r, "date", None)]), "raw log")

    coverage["timeline"] = _union_span(
        coverage.get("aw"),
        coverage.get("git"),
        coverage.get("terminal"),
        coverage.get("polylogue"),
        coverage.get("clipboard"),
        coverage.get("irc"),
        coverage.get("raw_log"),
    ) or DateSpan(floor, today)

    return coverage


def _discover_data_range() -> tuple[date, date]:
    """Find the earliest and latest dates with data across all sources."""
    coverage = _discover_source_coverage()
    spans = [span for key, span in coverage.items() if key != "timeline"]
    if not spans:
        fallback = date(2024, 10, 14)
        return fallback, date.today()
    data_start = min(span.start for span in spans)
    data_end = max(span.end for span in spans)
    print(f"  → Data range: {data_start} → {data_end}")
    return data_start, data_end


# ══════════════════════════════════════════════════════════════════════════════
# Orchestration: generate all ancestor levels for a date range
# ══════════════════════════════════════════════════════════════════════════════

def generate_hierarchy(start: date, end: date, output: Path, *, force: bool = False, dry_run: bool = False, skip_empty: bool = False) -> bool:
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
        return True

    coverage = _discover_source_coverage()

    # Pre-compute DayFeatures for the full range ONCE.
    # This loads all sources (Spotify 258K, etc.) but only once.
    print(f"  Pre-computing DayFeatures for {start} → {end}...")
    t_feat = time.monotonic()
    all_features = _safe(_build_features_verbose, start, end, coverage, default=[])
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
            if any((
                (f.active_hours or 0) > 0,
                (f.commit_count or 0) > 0,
                (f.sleep_hours or 0) > 0,
                (f.daily_steps or 0) > 0,
                (f.chat_sessions or 0) > 0,
                (f.command_count or 0) > 0,
                (f.browsing_visits or 0) > 0,
                (f.messenger_messages or 0) > 0,
                (f.bookmarks_added or 0) > 0,
                (f.substance_doses or 0) > 0,
                f.stress_avg is not None,
                f.heart_rate_avg is not None,
                f.hrv_rmssd is not None,
            )):
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
        git_window = _coverage_dates(coverage, "git", start, end)
        if git_window:
            for g in _safe(_git_daily, start=git_window[0], end=git_window[1], default=[]):
                git_days.add(g.date)
        days_with_data.update(git_days)
        print(f"    + git: {len(git_days)} days")
        # Days with Polylogue summaries
        from ..sources.polylogue import day_session_summaries as _poly_days
        poly_days = set()
        poly_window = _coverage_dates(coverage, "polylogue", start, end)
        if poly_window:
            for p in _safe(_poly_days, start=poly_window[0], end=poly_window[1], default=[]):
                poly_days.add(p.date)
        days_with_data.update(poly_days)
        print(f"    + polylogue: {len(poly_days)} days")
        # Days with terminal sessions
        from ..sources.terminal import shell_sessions as _shell_sessions
        shell_days = set()
        terminal_window = _coverage_dates(coverage, "terminal", start, end)
        if terminal_window:
            s_dt, e_dt = date_to_dt_range(terminal_window[0], terminal_window[1])
            for sh in _safe(_shell_sessions, start=s_dt, end=e_dt, default=[]):
                shell_days.add(sh.start.date())
        days_with_data.update(shell_days)
        print(f"    + terminal: {len(shell_days)} days")
        # Days with web/social signals
        from ..sources.web import daily_browsing as _web_days
        from ..sources.exports import daily_messenger_activity as _msg_days, daily_raindrop_activity as _bm_days
        web_window = _coverage_dates(coverage, "web", start, end)
        msg_window = _coverage_dates(coverage, "messenger", start, end)
        bm_window = _coverage_dates(coverage, "raindrop", start, end)
        web_days = {w.date for w in _safe(_web_days, start=web_window[0], end=web_window[1], default=[])} if web_window else set()
        msg_days = {m.date for m in _safe(_msg_days, start=msg_window[0], end=msg_window[1], default=[])} if msg_window else set()
        bm_days = {b.date for b in _safe(_bm_days, start=bm_window[0], end=bm_window[1], default=[])} if bm_window else set()
        days_with_data.update(web_days)
        days_with_data.update(msg_days)
        days_with_data.update(bm_days)
        print(f"    + web/social: {len(web_days | msg_days | bm_days)} days")
        # Days with reddit
        from ..sources.reddit import daily_activity as _reddit_daily
        reddit_days = set()
        reddit_window = _coverage_dates(coverage, "reddit", start, end)
        if reddit_window:
            for r in _safe(_reddit_daily, start=reddit_window[0], end=reddit_window[1], default=[]):
                reddit_days.add(r.date)
        days_with_data.update(reddit_days)
        print(f"    + reddit: {len(reddit_days)} days")

        capture_days = _capture_days_with_data(coverage, start, end)
        for label, source_days in capture_days.items():
            days_with_data.update(source_days)
            print(f"    + {label.replace('_', ' ')}: {len(source_days)} days")

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
    batch = _safe(BatchSources, start, end, coverage, default=None)
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
        if generate_overview(output, force=force, data_start=start, data_end=end, all_features=all_features, batch=batch):
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
    success = grand_failed == 0
    print(f"\n{'═' * 60}")
    print(f"  {'✓ Scaffold Complete' if success else '✗ Scaffold Failed'}")
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
    return success


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generate narrative scaffold from lynchpin sources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output", "-o", type=Path,
                        default=Path("retrospective/scaffold"),
                        help="Output directory (default: retrospective/scaffold/)")
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
        if not generate_hierarchy(d, d, output, force=args.force, skip_empty=args.skip_empty):
            sys.exit(1)
        return

    if args.start and args.end:
        s = date.fromisoformat(args.start)
        e = date.fromisoformat(args.end)
        if not generate_hierarchy(s, e, output, force=args.force, dry_run=args.dry_run, skip_empty=args.skip_empty):
            sys.exit(1)
        return

    if args.start or args.end:
        print("Both --start and --end are required for range generation", file=sys.stderr)
        sys.exit(1)

    # Full dataset — default to skip-empty for auto-discovered ranges
    print("Discovering data range...")
    data_start, data_end = _discover_data_range()
    print(f"Data range: {data_start} → {data_end}")
    if not generate_hierarchy(data_start, data_end, output, force=args.force, dry_run=args.dry_run,
                              skip_empty=args.skip_empty or not (args.start and args.end)):
        sys.exit(1)


if __name__ == "__main__":
    main()
