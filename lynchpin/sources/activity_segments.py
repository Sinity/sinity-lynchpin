"""Activity segmentation from raw AW events.

Builds a higher-level picture of what was happening than app_sessions:
- Raw AW events (~2.5s intervals) → RLE spans → context-classified segments
- Uses 10-min sliding windows to detect dominant activity context
- Merges consecutive same-context windows into segments

A day of 38K raw events → ~1000 RLE spans → ~40 meaningful segments.

Context families: ai, coding, reading, browsing, media, comms, other
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from ..core.config import get_config
from ..core.primitives import logical_date, date_to_dt_range

__all__ = [
    "ActivitySegment",
    "DaySegmentation",
    "segment_day",
    "segment_range",
]


@dataclass(frozen=True)
class ActivitySegment:
    """A coherent period of one dominant activity type."""
    start: datetime
    end: datetime
    duration_min: float
    context: str          # ai, coding, reading, browsing, media, comms, other
    purity: float         # 0-1: how much of the segment was the dominant context
    has_ai: bool          # any codex/claude activity during segment
    projects: tuple[str, ...]
    window_count: int     # how many 10-min windows this spans


@dataclass(frozen=True)
class DaySegmentation:
    """A full day segmented into activity contexts."""
    date: date
    segments: tuple[ActivitySegment, ...]
    total_active_min: float
    context_hours: dict[str, float]  # context → hours
    transition_count: int
    ai_hours: float       # total time with AI active


# ── Raw event processing ──────────────────────────────────────────────────

def _load_raw_events(d: date) -> list[dict]:
    """Load raw AW window events for a date from SQLite."""
    cfg = get_config()
    db_path = cfg.activitywatch_db
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))

    # Find ALL window buckets (hostname may have changed — union all)
    buckets = conn.execute("SELECT id, name, type FROM buckets WHERE type='currentwindow'").fetchall()
    if not buckets:
        conn.close()
        return []

    # Convert date to nanosecond timestamps (UTC)
    start_ns = int(datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1e9)
    end_ns = int(datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1e9)

    # Union events from all window buckets
    rows = []
    for bid, _, _ in buckets:
        rows.extend(conn.execute("""
            SELECT starttime, data FROM events
            WHERE bucketrow = ? AND starttime >= ? AND starttime < ?
        """, (bid, start_ns, end_ns)).fetchall())
    rows.sort()
    conn.close()

    if not rows:
        return []

    # Run-length encode: collapse consecutive identical (app, title)
    spans = []
    prev = json.loads(rows[0][1])
    span_start_ns = rows[0][0]

    for ts, data_json in rows[1:]:
        data = json.loads(data_json)
        if data.get('app') != prev.get('app') or data.get('title') != prev.get('title'):
            dur_s = (ts - span_start_ns) / 1e9
            if dur_s >= 3:  # skip micro-spans
                spans.append({
                    'start_ns': span_start_ns,
                    'dur_s': dur_s,
                    'app': prev.get('app', ''),
                    'title': prev.get('title', ''),
                })
            span_start_ns = ts
            prev = data

    # Final span
    if rows:
        dur_s = 2.5  # last event has no successor
        spans.append({
            'start_ns': span_start_ns,
            'dur_s': dur_s,
            'app': prev.get('app', ''),
            'title': prev.get('title', ''),
        })

    return spans


# ── Context classification ────────────────────────────────────────────────

def _classify_context(app: str, title: str) -> str:
    """Classify a span into an activity context from app + title.

    Context families:
    - work: terminal, AI tools (codex/claude/chatgpt), coding
    - reading: articles, documentation, wikis
    - social: reddit, twitter
    - browsing: other web
    - media: youtube, music, video
    - comms: email, chat
    """
    t = title.lower()

    # AI tool usage and terminal are both "work"
    if 'codex' in t or 'claude code' in t or '✳ claude' in t:
        return 'work'
    if app == 'kitty' or app == 'foot':
        return 'work'

    # Browser classification by URL/title content
    if 'chrome' in app or 'firefox' in app or 'zen' in app or 'floorp' in app:
        if 'music.youtube' in t:
            return 'media'
        if 'youtube.com/watch' in t:
            return 'media'
        if 'reddit.com' in t or 'x.com' in t or 'twitter.com' in t:
            return 'social'
        if 'mail.google' in t or 'gmail' in t:
            return 'comms'
        if 'chatgpt.com' in t:
            return 'work'
        if 'lesswrong.com' in t or 'substack.com' in t or 'wikipedia.org' in t:
            return 'reading'
        if 'github.com' in t or 'docs.rs' in t or 'rust-book' in t or 'stackoverflow' in t:
            return 'reading'
        return 'browsing'

    if 'mpv' in app:
        return 'media'
    if 'weechat' in app or 'discord' in app or 'slack' in app:
        return 'comms'

    return 'other'


def _extract_project(title: str) -> str | None:
    """Extract project name from terminal title."""
    if '/realm/project/' in title:
        parts = title.split('/realm/project/')[1].split('/')
        proj = parts[0].split(':')[0].split(' ')[0]
        if proj:
            return proj
    return None


# ── Segmentation ──────────────────────────────────────────────────────────

_WINDOW_MIN = 10


def segment_day(d: date) -> DaySegmentation | None:
    """Segment one day into activity contexts."""
    spans = _load_raw_events(d)
    if not spans:
        return None

    def ns_to_dt(ns):
        return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).astimezone()

    # Build 10-minute windows
    first_ns = spans[0]['start_ns']
    last_ns = spans[-1]['start_ns'] + int(spans[-1]['dur_s'] * 1e9)
    window_ns = int(_WINDOW_MIN * 60 * 1e9)

    windows = []
    cursor = first_ns
    while cursor < last_ns:
        w_end = cursor + window_ns
        w_spans = [s for s in spans if s['start_ns'] >= cursor and s['start_ns'] < w_end]

        if w_spans:
            ctx_time: dict[str, float] = defaultdict(float)
            projects: set[str] = set()
            has_ai = False

            for s in w_spans:
                ctx = _classify_context(s['app'], s['title'])
                ctx_time[ctx] += s['dur_s']
                if ctx == 'ai':
                    has_ai = True
                proj = _extract_project(s['title'])
                if proj:
                    projects.add(proj)

            total_s = sum(ctx_time.values())
            dominant = max(ctx_time, key=ctx_time.get)
            purity = ctx_time[dominant] / total_s if total_s > 0 else 0

            windows.append({
                'start_ns': cursor,
                'dominant': dominant,
                'purity': purity,
                'has_ai': has_ai,
                'projects': projects,
                'active_s': total_s,
            })

        cursor += window_ns

    if not windows:
        return None

    # Hysteresis segmentation: only switch context when the new one
    # dominates for MIN_PERSIST consecutive windows (avoids oscillation
    # between e.g., terminal and browser during interleaved workflows)
    MIN_PERSIST = 3  # new context must persist 3 windows (~6 min)

    segments: list[ActivitySegment] = []
    current_ctx = windows[0]['dominant']
    run: list[dict] = [windows[0]]
    candidate_ctx: str | None = None
    persist_count = 0

    for w in windows[1:]:
        if w['dominant'] == current_ctx:
            run.append(w)
            persist_count = 0
            candidate_ctx = None
        elif w['dominant'] == candidate_ctx:
            persist_count += 1
            run.append(w)
            if persist_count >= MIN_PERSIST:
                # Commit current segment (up to where candidate started)
                commit_point = len(run) - persist_count - 1
                if commit_point > 0:
                    segments.append(_build_segment(run[:commit_point]))
                    run = run[commit_point:]
                current_ctx = candidate_ctx
                persist_count = 0
                candidate_ctx = None
        else:
            run.append(w)
            candidate_ctx = w['dominant']
            persist_count = 1

    if run:
        segments.append(_build_segment(run))

    # Compute day-level stats
    ctx_hours: dict[str, float] = defaultdict(float)
    for seg in segments:
        ctx_hours[seg.context] += seg.duration_min / 60

    ai_hours = sum(seg.duration_min for seg in segments if seg.has_ai) / 60
    total_min = sum(seg.duration_min for seg in segments)
    transitions = len(segments) - 1

    return DaySegmentation(
        date=d,
        segments=tuple(segments),
        total_active_min=round(total_min, 1),
        context_hours={k: round(v, 2) for k, v in ctx_hours.items()},
        transition_count=transitions,
        ai_hours=round(ai_hours, 2),
    )


def _build_segment(run: list[dict]) -> ActivitySegment:
    """Build an ActivitySegment from a run of same-context windows."""
    start = datetime.fromtimestamp(run[0]['start_ns'] / 1e9, tz=timezone.utc).astimezone()
    end = datetime.fromtimestamp(
        (run[-1]['start_ns'] + int(_WINDOW_MIN * 60 * 1e9)) / 1e9, tz=timezone.utc
    ).astimezone()
    dur_min = len(run) * _WINDOW_MIN
    avg_purity = sum(w['purity'] for w in run) / len(run)
    has_ai = any(w['has_ai'] for w in run)
    projects: set[str] = set()
    for w in run:
        projects.update(w['projects'])

    return ActivitySegment(
        start=start, end=end, duration_min=dur_min,
        context=run[0]['dominant'], purity=round(avg_purity, 3),
        has_ai=has_ai, projects=tuple(sorted(projects)),
        window_count=len(run),
    )


def segment_range(*, start: date, end: date) -> list[DaySegmentation]:
    """Segment a range of days."""
    results = []
    d = start
    while d <= end:
        seg = segment_day(d)
        if seg is not None:
            results.append(seg)
        d += timedelta(days=1)
    return results
