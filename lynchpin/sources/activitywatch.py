"""ActivityWatch source: raw events → focus spans → sessions → deep work, circadian, loops, fragmentation, attention.

Graduated API — each function builds on the one before it:
  events() → active_intervals() → focus_spans() → app_sessions() → deep_work()
                                                 → circadian() → loops() → fragmentation() → attention()

Optional sensitive-content analysis should be layered on top of
`window_events()`, `web_events()`, or `focus_spans()` by matching curated
domains or application names and then trimming with AFK-aware intervals. Those
heuristics are intentionally policy-specific, so they stay out of the exported
API surface.
"""

from __future__ import annotations

import functools
import json
import math
import sqlite3
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, Optional, Sequence

from ..core.cache import file_signature, persistent_cache
from ..core.classify import classify, Attribution
from ..core.title_features import extract_title_features
from ..core.config import get_config
from ..core.primitives import (
    TopN, Group, group_by_gap,
    merge_intervals, intersect_intervals, split_by_day, split_by_hour, duration_s,
    Interval,
)
from ..core.parse import as_local

__all__ = [
    "AWEvent",
    "FocusSpan",
    "FocusTimelineSpan",
    "AppSession",
    "DeepWorkBlock",
    "CircadianProfile",
    "FocusLoop",
    "FragmentationMetrics",
    "AttentionMetrics",
    "events",
    "window_events",
    "afk_events",
    "web_events",
    "active_intervals",
    "afk_intervals",
    "active_seconds_by_date",
    "focus_spans",
    "focus_timeline",
    "app_sessions",
    "deep_work",
    "circadian",
    "loops",
    "fragmentation",
    "attention",
    "SustainedFocus",
    "sustained_focus",
    "AWDayActivity",
    "daily_activity",
]

# ══════════════════════════════════════════════════════════════════════════════
# Layer 0: Raw SQLite access
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class AWEvent:
    bucket: str
    start: datetime
    end: datetime
    data: Dict[str, object]


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path).expanduser() if db_path else get_config().activitywatch_db
    return sqlite3.connect(str(path))


def events(
    bucket_prefix: str, *, start: datetime, end: datetime, db_path: Optional[Path] = None
) -> Iterator[AWEvent]:
    since_ns = int(start.timestamp() * 1_000_000_000)
    until_ns = int(end.timestamp() * 1_000_000_000)
    query = (
        "SELECT b.name, e.starttime, e.endtime, e.data "
        "FROM events e JOIN buckets b ON b.id = e.bucketrow "
        "WHERE b.name LIKE ? AND e.starttime < ? AND e.endtime > ? ORDER BY e.starttime"
    )
    with _connect(db_path) as conn:
        for bucket, start_ns, end_ns, payload in conn.execute(query, (f"{bucket_prefix}%", until_ns, since_ns)):
            if start_ns is None or end_ns is None:
                continue
            s = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc)
            e = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=timezone.utc)
            data: Dict[str, object] = {}
            if payload:
                try:
                    data = json.loads(payload if isinstance(payload, str) else payload.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                    pass
            yield AWEvent(bucket=bucket, start=s, end=e, data=data)


def window_events(**kw) -> Iterator[AWEvent]: return events("aw-watcher-window_", **kw)
def afk_events(**kw) -> Iterator[AWEvent]: return events("aw-watcher-afk_", **kw)
def web_events(**kw) -> Iterator[AWEvent]: return events("aw-watcher-web_", **kw)


# ══════════════════════════════════════════════════════════════════════════════
# Layer 1: Typed intervals
# ══════════════════════════════════════════════════════════════════════════════

_ACTIVE_STATUSES = {"not-afk", "active", "present"}
_AFK_STATUSES = {"afk", "away"}


def active_intervals(start: datetime, end: datetime) -> list[Interval]:
    return merge_intervals(
        (as_local(e.start), as_local(e.end))
        for e in afk_events(start=as_local(start), end=as_local(end))
        if str((e.data or {}).get("status") or "").strip().lower() in _ACTIVE_STATUSES
    )


def afk_intervals(start: datetime, end: datetime) -> list[Interval]:
    return merge_intervals(
        (as_local(e.start), as_local(e.end))
        for e in afk_events(start=as_local(start), end=as_local(end))
        if str((e.data or {}).get("status") or "").strip().lower() in _AFK_STATUSES
    )


def active_seconds_by_date(start: date, end: date) -> dict[date, float]:
    s = datetime.combine(start, time.min)
    e = datetime.combine(end + timedelta(days=1), time.min)
    totals: dict[date, float] = {}
    for iv in active_intervals(s, e):
        for day, seg in split_by_day(*iv):
            totals[day] = totals.get(day, 0) + duration_s(seg)
    return totals


# ══════════════════════════════════════════════════════════════════════════════
# Layer 2: Focus spans — the core AW algorithm (sweep-line interval merge)
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FocusSpan:
    start: datetime
    end: datetime
    kind: str  # "focused" | "afk" | "active_unknown"
    app: str | None
    title: str | None
    mode: str | None
    project: str | None
    keypress_count: int = 0
    keylog_state: str = "not_requested"

    @property
    def duration_s(self) -> float:
        return max((self.end - self.start).total_seconds(), 0)

    @property
    def date(self) -> date:
        return self.start.date()


@dataclass(frozen=True)
class FocusTimelineSpan:
    start: datetime
    end: datetime
    kind: str  # "focused" | "afk" | "active_unknown" | "coverage_gap"
    app: str | None
    title: str | None
    mode: str | None
    project: str | None
    source: str
    keypress_count: int = 0
    keylog_state: str = "not_requested"

    @property
    def duration_s(self) -> float:
        return max((self.end - self.start).total_seconds(), 0)

    @property
    def date(self) -> date:
        return self.start.date()


@dataclass(frozen=True)
class _WindowSpan:
    start: datetime
    end: datetime
    app: str
    title: str
    mode: str | None
    project: str | None


def focus_spans(*, start: datetime, end: datetime, min_duration_s: float = 0.0) -> list[FocusSpan]:
    """AFK-trimmed classified focus timeline."""
    return list(_focus_spans_cached(as_local(start), as_local(end), min_duration_s))


@functools.lru_cache(maxsize=16)
def _focus_spans_cached(start: datetime, end: datetime, min_dur: float) -> tuple[FocusSpan, ...]:
    active = active_intervals(start, end)
    afk = afk_intervals(start, end)
    windows = _window_spans(start, end, active=active, min_duration_s=0.0)

    # Collect all boundary points
    boundaries = {start, end}
    for s, e in active:
        boundaries.add(max(s, start)); boundaries.add(min(e, end))
    for s, e in afk:
        boundaries.add(max(s, start)); boundaries.add(min(e, end))
    for w in windows:
        boundaries.add(max(w.start, start)); boundaries.add(min(w.end, end))
    # Day boundaries
    cursor = datetime.combine(start.date(), time.min, tzinfo=start.tzinfo) + timedelta(days=1)
    while cursor < end:
        boundaries.add(cursor)
        cursor += timedelta(days=1)

    ordered = sorted(boundaries)
    spans: list[FocusSpan] = []
    a_idx = w_idx = afk_idx = 0

    for left, right in zip(ordered, ordered[1:]):
        if right <= left:
            continue
        # Advance indices past segments that end before left
        while afk_idx < len(afk) and afk[afk_idx][1] <= left:
            afk_idx += 1
        while a_idx < len(active) and active[a_idx][1] <= left:
            a_idx += 1
        while w_idx < len(windows) and windows[w_idx].end <= left:
            w_idx += 1

        # Priority: AFK → window → active_unknown
        if afk_idx < len(afk) and afk[afk_idx][0] <= left and afk[afk_idx][1] >= right:
            spans.append(FocusSpan(start=left, end=right, kind="afk", app=None, title=None, mode=None, project=None))
        elif w_idx < len(windows) and windows[w_idx].start <= left and windows[w_idx].end >= right:
            w = windows[w_idx]
            spans.append(FocusSpan(start=left, end=right, kind="focused", app=w.app, title=w.title, mode=w.mode, project=w.project))
        elif a_idx < len(active) and active[a_idx][0] <= left and active[a_idx][1] >= right:
            spans.append(FocusSpan(start=left, end=right, kind="active_unknown", app=None, title=None, mode=None, project=None))

    merged = [s for s in _merge_adjacent(spans) if s.duration_s >= min_dur]
    return tuple(_attach_keypress_counts(merged, start=start, end=end))


def focus_timeline(
    *, start: datetime, end: datetime, heal_afk: bool = True, min_duration_s: float = 0.0,
) -> list[FocusTimelineSpan]:
    """Prompt-facing focus timeline with explicit AFK coverage gaps."""
    start_local = as_local(start)
    end_local = as_local(end)
    base = [
        FocusTimelineSpan(
            start=span.start,
            end=span.end,
            kind=span.kind,
            app=span.app,
            title=span.title,
            mode=span.mode,
            project=span.project,
            source="aw_trimmed",
        )
        for span in focus_spans(start=start_local, end=end_local, min_duration_s=0.0)
    ]
    raw_windows = _window_spans(start_local, end_local, active=None, min_duration_s=0.0)
    press_times, keylog_state = _keypress_timestamps(start_local, end_local)

    gap_spans: list[FocusTimelineSpan] = []
    for gap_start, gap_end in _coverage_gaps(start_local, end_local):
        gap_windows = _intersect_window_spans(raw_windows, gap_start, gap_end)
        gap_keypresses = (
            _count_presses_in_intervals(press_times, [(gap_start, gap_end)])[0]
            if press_times else 0
        )
        if heal_afk and gap_keypresses > 0:
            if gap_windows:
                gap_spans.extend(
                    FocusTimelineSpan(
                        start=window.start,
                        end=window.end,
                        kind="focused",
                        app=window.app,
                        title=window.title,
                        mode=window.mode,
                        project=window.project,
                        source="afk_gap_healed",
                    )
                    for window in gap_windows
                )
            else:
                gap_spans.append(FocusTimelineSpan(
                    start=gap_start,
                    end=gap_end,
                    kind="active_unknown",
                    app=None,
                    title=None,
                    mode=None,
                    project=None,
                    source="afk_gap_healed",
                ))
            continue

        if gap_windows:
            gap_spans.extend(
                FocusTimelineSpan(
                    start=window.start,
                    end=window.end,
                    kind="coverage_gap",
                    app=window.app,
                    title=window.title,
                    mode=window.mode,
                    project=window.project,
                    source="aw_afk_missing",
                )
                for window in gap_windows
            )
        else:
            gap_spans.append(FocusTimelineSpan(
                start=gap_start,
                end=gap_end,
                kind="coverage_gap",
                app=None,
                title=None,
                mode=None,
                project=None,
                source="aw_afk_missing",
            ))

    ordered = sorted([*base, *gap_spans], key=lambda span: (span.start, span.end, span.kind, span.source))
    merged = [
        span
        for span in _merge_timeline_adjacent(ordered)
        if span.duration_s >= min_duration_s
    ]
    return _attach_keypress_counts(
        merged,
        start=start_local,
        end=end_local,
        keylog_state=keylog_state,
        press_times=press_times,
    )


def _window_spans(
    start: datetime, end: datetime, *, active: list[Interval] | None, min_duration_s: float,
) -> list[_WindowSpan]:
    """Window events, optionally intersected with AFK-active intervals.

    AW stores zero-duration window events — each event's effective end is the
    next event's start. We compute implicit durations before intersecting.
    """
    raw_events = list(window_events(start=start, end=end))
    if not raw_events:
        return []

    # Compute effective durations: each event lasts until the next one starts
    timed: list[tuple[datetime, datetime, AWEvent]] = []
    for i, evt in enumerate(raw_events):
        evt_start = as_local(evt.start)
        if i + 1 < len(raw_events):
            evt_end = as_local(raw_events[i + 1].start)
        else:
            evt_end = evt_start + timedelta(seconds=5)  # last event fallback
        if evt_end <= evt_start:
            evt_end = evt_start + timedelta(seconds=1)
        timed.append((evt_start, evt_end, evt))

    raw_spans: list[_WindowSpan] = []
    iv_idx = 0
    start_local = as_local(start)
    end_local = as_local(end)
    for evt_start, evt_end, evt in timed:
        if not evt.data.get("app"):
            continue
        title = str(evt.data.get("title") or "(untitled)").strip()
        if title.lower() == "application not responding":
            continue
        app = str(evt.data["app"])
        attr = classify(app=app, title=title, cwd=str(evt.data.get("cwd") or ""),
                        url=str(evt.data.get("url") or ""), source="activitywatch.window")
        # Enrich with title feature extraction — better project + AI detection
        feat = extract_title_features(app, title)
        project = attr.project or feat.project
        mode = attr.mode if attr.mode != "unknown" else None
        # Title features can improve mode when classify returns unknown
        if mode is None and feat.domain_category:
            mode = feat.domain_category
        if mode is None and feat.is_ai_tool:
            mode = "coding"
        if active is None:
            overlaps = [(max(evt_start, start_local), min(evt_end, end_local))]
        else:
            overlaps, iv_idx = intersect_intervals(evt_start, evt_end, active, iv_idx)
        for ov_start, ov_end in overlaps:
            for day, (seg_s, seg_e) in split_by_day(ov_start, ov_end):
                raw_spans.append(_WindowSpan(
                    start=seg_s, end=seg_e, app=app, title=title,
                    mode=mode, project=project,
                ))
    # Linearize merges consecutive same-app spans; filter short ones after merge
    merged = _linearize_windows(raw_spans)
    return [w for w in merged if (w.end - w.start).total_seconds() >= min_duration_s]


def _attributed_windows(start: datetime, end: datetime, active: list[Interval]) -> list[_WindowSpan]:
    return _window_spans(start, end, active=active, min_duration_s=10.0)


def _coverage_gaps(start: datetime, end: datetime) -> list[Interval]:
    rows = sorted(
        (as_local(event.start), as_local(event.end))
        for event in afk_events(start=as_local(start), end=as_local(end))
        if event.start is not None and event.end is not None
    )
    if not rows:
        return [(start, end)]

    merged = merge_intervals(rows)
    gaps: list[Interval] = []
    cursor = start
    for left, right in merged:
        if left > cursor:
            gaps.append((cursor, min(left, end)))
        cursor = max(cursor, right)
        if cursor >= end:
            break
    if cursor < end:
        gaps.append((cursor, end))
    return [(left, right) for left, right in gaps if right > left]


def _intersect_window_spans(spans: Sequence[_WindowSpan], start: datetime, end: datetime) -> list[_WindowSpan]:
    result: list[_WindowSpan] = []
    for span in spans:
        if span.end <= start:
            continue
        if span.start >= end:
            break
        ov_start = max(span.start, start)
        ov_end = min(span.end, end)
        if ov_end > ov_start:
            result.append(replace(span, start=ov_start, end=ov_end))
    return _linearize_windows(result)


def _keypress_timestamps(start: datetime, end: datetime) -> tuple[tuple[datetime, ...], str]:
    try:
        from .keylog import has_coverage, keypresses
    except Exception:
        return (), "error"

    if not has_coverage(start=start, end=end):
        return (), "missing"
    try:
        return tuple(event.ts for event in keypresses(start=start, end=end)), "covered"
    except Exception:
        return (), "error"


def _count_presses_in_intervals(
    press_times: Sequence[datetime], intervals: Sequence[Interval],
) -> list[int]:
    if not intervals:
        return []
    counts: list[int] = []
    cursor = 0
    for left, right in intervals:
        start_idx = bisect_left(press_times, left, cursor)
        end_idx = bisect_left(press_times, right, start_idx)
        counts.append(max(end_idx - start_idx, 0))
        cursor = start_idx
    return counts


def _attach_keypress_counts(
    spans: Sequence[FocusSpan | FocusTimelineSpan],
    *,
    start: datetime,
    end: datetime,
    keylog_state: str | None = None,
    press_times: Sequence[datetime] | None = None,
) -> list[FocusSpan | FocusTimelineSpan]:
    if not spans:
        return []
    if press_times is None or keylog_state is None:
        press_times, keylog_state = _keypress_timestamps(start, end)
    counts = (
        _count_presses_in_intervals(press_times, [(span.start, span.end) for span in spans])
        if press_times else [0] * len(spans)
    )
    return [
        replace(span, keypress_count=count, keylog_state=keylog_state)
        for span, count in zip(spans, counts)
    ]


def _linearize_windows(spans: Sequence[_WindowSpan]) -> list[_WindowSpan]:
    """Resolve overlapping windows by preferring richer attribution."""
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: (s.start, s.end, s.app, s.title))
    emitted: list[_WindowSpan] = []
    current = ordered[0]
    for span in ordered[1:]:
        if span.start >= current.end:
            _append_or_merge_win(emitted, current)
            current = span
        else:
            if span.start > current.start:
                clipped = replace(current, end=span.start)
                if clipped.end > clipped.start:
                    _append_or_merge_win(emitted, clipped)
            current = span if _win_score(span) >= _win_score(current) else current
    _append_or_merge_win(emitted, current)
    return emitted


def _win_score(s: _WindowSpan) -> tuple[int, int, float]:
    return (1 if s.project else 0, 1 if s.mode else 0, (s.end - s.start).total_seconds())


def _append_or_merge_win(target: list[_WindowSpan], span: _WindowSpan) -> None:
    if span.end <= span.start:
        return
    if target:
        prev = target[-1]
        if (prev.app == span.app and prev.title == span.title and prev.mode == span.mode
                and prev.project == span.project and prev.start.date() == span.start.date()
                and prev.end >= span.start):
            target[-1] = replace(prev, end=max(prev.end, span.end))
            return
    target.append(span)


def _merge_adjacent(spans: Sequence[FocusSpan]) -> Iterator[FocusSpan]:
    if not spans:
        return
    current = spans[0]
    for s in spans[1:]:
        if (current.kind == s.kind and current.app == s.app and current.title == s.title
                and current.mode == s.mode and current.project == s.project
                and current.date == s.date and current.end >= s.start):
            current = FocusSpan(
                start=current.start, end=max(current.end, s.end), kind=current.kind,
                app=current.app, title=current.title, mode=current.mode, project=current.project,
                keypress_count=current.keypress_count + s.keypress_count,
                keylog_state=current.keylog_state,
            )
        else:
            yield current
            current = s
    yield current


def _merge_timeline_adjacent(spans: Sequence[FocusTimelineSpan]) -> Iterator[FocusTimelineSpan]:
    if not spans:
        return
    current = spans[0]
    for span in spans[1:]:
        if (
            current.kind == span.kind
            and current.app == span.app
            and current.title == span.title
            and current.mode == span.mode
            and current.project == span.project
            and current.source == span.source
            and current.date == span.date
            and current.end >= span.start
        ):
            current = FocusTimelineSpan(
                start=current.start,
                end=max(current.end, span.end),
                kind=current.kind,
                app=current.app,
                title=current.title,
                mode=current.mode,
                project=current.project,
                source=current.source,
                keypress_count=current.keypress_count + span.keypress_count,
                keylog_state=current.keylog_state,
            )
        else:
            yield current
            current = span
    yield current


# ══════════════════════════════════════════════════════════════════════════════
# Layer 3: Derived analytics
# ══════════════════════════════════════════════════════════════════════════════

# ── App sessions ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AppSession:
    app: str
    start: datetime
    end: datetime
    duration_s: float
    title_dominant: str
    titles: tuple[str, ...]
    mode: str | None
    project: str | None
    interruptions: int


def app_sessions(*, start: datetime, end: datetime, min_duration_s: float = 60) -> list[AppSession]:
    spans = [
        s for s in focus_spans(start=start, end=end, min_duration_s=10.0)
        if s.kind == "focused" and s.app and s.title
    ]
    sessions: list[AppSession] = []
    for g in group_by_gap(
        spans, start_of=lambda s: s.start, end_of=lambda s: s.end,
        max_gap=120, absorb_interruption=30,
        compatible=lambda a, b: a.app == b.app and a.date == b.date,
    ):
        wall = duration_s((g.start, g.end))
        if wall < min_duration_s:
            continue
        modes, projects = TopN(1), TopN(1)
        title_dur: dict[str, float] = {}
        for s in g.items:
            d = s.duration_s
            if s.mode: modes.add(s.mode, d)
            if s.project: projects.add(s.project, d)
            title_dur[s.title] = title_dur.get(s.title, 0) + d
        top_title = max(title_dur, key=title_dur.get) if title_dur else ""
        sessions.append(AppSession(
            app=g.items[0].app, start=g.start, end=g.end, duration_s=round(wall, 3),
            title_dominant=top_title,
            titles=tuple(sorted(title_dur, key=title_dur.get, reverse=True)),
            mode=modes.dominant, project=projects.dominant, interruptions=g.interruptions,
        ))
    return sessions


# ── Deep work blocks ──────────────────────────────────────────────────────────

_PRODUCTIVE_MODES = {"coding", "research", "writing", "planning", "chat"}


@dataclass(frozen=True)
class DeepWorkBlock:
    start: datetime
    end: datetime
    duration_min: float
    project: str | None
    mode: str
    focus_ratio: float
    app_switches: int


def deep_work(*, start: datetime, end: datetime, min_minutes: float = 30, max_interruption_ratio: float = 0.15) -> list[DeepWorkBlock]:
    productive = [s for s in app_sessions(start=start, end=end) if s.project or (s.mode or "") in _PRODUCTIVE_MODES]
    blocks: list[DeepWorkBlock] = []
    for g in group_by_gap(
        productive, start_of=lambda s: s.start, end_of=lambda s: s.end,
        max_gap=600, absorb_interruption=300,
        compatible=_deep_compatible,
    ):
        wall = duration_s((g.start, g.end))
        productive_s = sum(s.duration_s for s in g.items)
        ratio = productive_s / wall if wall > 0 else 0
        if wall / 60 >= min_minutes and ratio >= (1 - max_interruption_ratio):
            modes, projects = TopN(1), TopN(1)
            for s in g.items:
                if s.mode: modes.add(s.mode, s.duration_s)
                if s.project: projects.add(s.project, s.duration_s)
            switches = sum(1 for a, b in zip(g.items, g.items[1:]) if a.app != b.app)
            blocks.append(DeepWorkBlock(
                start=g.start, end=g.end, duration_min=round(wall / 60, 1),
                project=projects.dominant, mode=modes.dominant or "unknown",
                focus_ratio=round(ratio, 3), app_switches=switches,
            ))
    return blocks


def _deep_compatible(a: AppSession, b: AppSession) -> bool:
    if a.project and b.project: return a.project == b.project
    if a.mode and b.mode: return a.mode == b.mode and a.mode in _PRODUCTIVE_MODES
    return False


# ── Circadian profiles ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CircadianProfile:
    date: date
    hour: int
    active_min: float
    recovery_min: float
    dominant_mode: str | None
    dominant_project: str | None


def circadian(*, start: date, end: date) -> list[CircadianProfile]:
    s = datetime.combine(start, time.min)
    e = datetime.combine(end + timedelta(days=1), time.min)
    buckets: dict[tuple[date, int], tuple[TopN, TopN, float, float]] = {}
    for span in focus_spans(start=s, end=e, min_duration_s=30):
        for hour, seg in split_by_hour(span.start, span.end):
            key = (span.date, hour)
            modes, projects, active, recovery = buckets.get(key, (TopN(1), TopN(1), 0.0, 0.0))
            mins = duration_s(seg) / 60
            if span.kind == "afk":
                recovery += mins
            else:
                active += mins
                if span.mode: modes.add(span.mode, mins)
                if span.project: projects.add(span.project, mins)
            buckets[key] = (modes, projects, active, recovery)
    result: list[CircadianProfile] = []
    for (d, h), (modes, projects, active, recovery) in sorted(buckets.items()):
        if active > 0 or recovery > 0:
            result.append(CircadianProfile(d, h, round(active, 1), round(recovery, 1), modes.dominant, projects.dominant))
    return result


# ── Focus loops (A↔B alternation) ────────────────────────────────────────────


@dataclass(frozen=True)
class FocusLoop:
    date: date
    start: datetime
    end: datetime
    duration_min: float
    span_count: int
    switch_count: int
    context_a: str
    context_b: str
    dominant_project: str | None


def loops(*, start: datetime, end: datetime, min_spans: int = 4, max_gap: float = 180) -> list[FocusLoop]:
    spans = [s for s in focus_spans(start=start, end=end) if s.kind == "focused" and s.app]
    by_day: dict[date, list[FocusSpan]] = {}
    for s in spans:
        by_day.setdefault(s.date, []).append(s)

    result: list[FocusLoop] = []
    for day in sorted(by_day):
        ds = by_day[day]
        i = 0
        while i <= len(ds) - min_spans:
            first, second = ds[i], ds[i + 1] if i + 1 < len(ds) else None
            if second is None or _ctx(first) == _ctx(second):
                i += 1; continue
            if (second.start - first.end).total_seconds() > max_gap:
                i += 1; continue
            ctx_a, ctx_b = _ctx(first), _ctx(second)
            collected = [first, second]
            expected = ctx_a
            j = i + 2
            while j < len(ds):
                span = ds[j]
                if (span.start - collected[-1].end).total_seconds() > max_gap: break
                ctx = _ctx(span)
                if ctx not in {ctx_a, ctx_b} or ctx != expected: break
                collected.append(span)
                expected = ctx_b if expected == ctx_a else ctx_a
                j += 1
            if len(collected) >= min_spans:
                dur = (collected[-1].end - collected[0].start).total_seconds() / 60
                if dur >= 8:
                    projects = TopN(1)
                    for s in collected:
                        if s.project: projects.add(s.project, s.duration_s)
                    result.append(FocusLoop(
                        date=day, start=collected[0].start, end=collected[-1].end,
                        duration_min=round(dur, 1), span_count=len(collected),
                        switch_count=len(collected) - 1,
                        context_a=f"{first.app}::{first.title}",
                        context_b=f"{second.app}::{second.title}",
                        dominant_project=projects.dominant,
                    ))
                    i = j; continue
            i += 1
    return result


def _ctx(s: FocusSpan) -> tuple[str | None, str | None]:
    return (s.app, s.title)


# ── Fragmentation ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FragmentationMetrics:
    date: date
    total_switches: int
    avg_focus_min: float
    longest_focus_min: float
    fragmentation: float  # 0=focused, 1=scattered


def fragmentation(*, start: date, end: date) -> list[FragmentationMetrics]:
    s = datetime.combine(start, time.min)
    e = datetime.combine(end + timedelta(days=1), time.min)
    sessions = app_sessions(start=s, end=e)
    by_day: dict[date, list[AppSession]] = {}
    for sess in sessions:
        by_day.setdefault(sess.start.date(), []).append(sess)

    result: list[FragmentationMetrics] = []
    for day, ds in sorted(by_day.items()):
        if len(ds) < 2: continue
        stretches = _focus_stretches(ds)
        if not stretches: continue
        longest = max(stretches)
        total = sum(stretches)
        result.append(FragmentationMetrics(
            date=day, total_switches=len(ds) - 1,
            avg_focus_min=round(total / len(stretches), 1),
            longest_focus_min=round(longest, 1),
            fragmentation=round(max(0, min(1, 1 - longest / total)), 3) if total > 0 else 0,
        ))
    return result


def _focus_stretches(sessions: Sequence[AppSession]) -> list[float]:
    if not sessions: return []
    stretches: list[float] = []
    key = _session_ctx(sessions[0])
    mins = sessions[0].duration_s / 60
    end = sessions[0].end
    for s in sessions[1:]:
        gap_min = max((s.start - end).total_seconds(), 0) / 60
        if _session_ctx(s) == key and gap_min <= 5:
            mins += gap_min + s.duration_s / 60
            end = s.end
        else:
            stretches.append(mins)
            key = _session_ctx(s)
            mins = s.duration_s / 60
            end = s.end
    stretches.append(mins)
    return stretches


def _session_ctx(s: AppSession) -> str:
    if s.project: return f"project:{s.project}"
    if s.mode: return f"mode:{s.mode}"
    return f"app:{s.app}"


# ── Project attention ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AttentionMetrics:
    date: date
    entropy: float
    gini: float
    top_project: str | None
    project_count: int


def attention(*, start: date, end: date) -> list[AttentionMetrics]:
    s = datetime.combine(start, time.min)
    e = datetime.combine(end + timedelta(days=1), time.min)
    spans = focus_spans(start=s, end=e, min_duration_s=60)
    by_day: dict[date, dict[str, float]] = {}
    for span in spans:
        if span.kind != "focused" or not span.project: continue
        bucket = by_day.setdefault(span.date, {})
        bucket[span.project] = bucket.get(span.project, 0) + span.duration_s

    result: list[AttentionMetrics] = []
    for day, projects in sorted(by_day.items()):
        if not projects: continue
        total = sum(projects.values())
        probs = [v / total for v in projects.values()]
        entropy = -sum(p * math.log2(p) for p in probs if p > 0)
        sorted_vals = sorted(projects.values())
        n = len(sorted_vals)
        gini = (2 * sum((i + 1) * v for i, v in enumerate(sorted_vals)) / (n * total) - (n + 1) / n) if n > 0 and total > 0 else 0
        top = max(projects, key=projects.get)
        result.append(AttentionMetrics(date=day, entropy=round(entropy, 3), gini=round(gini, 3), top_project=top, project_count=n))
    return result


# ── Sustained focus (mode-agnostic) ──────────────────────────────────────────


@dataclass(frozen=True)
class SustainedFocus:
    """A sustained period of computer activity without significant AFK breaks.

    Unlike deep_work, this doesn't filter by mode — it measures ANY sustained
    active period. The mode/project are informational, not filtering criteria.
    """
    start: datetime
    end: datetime
    duration_min: float
    dominant_mode: str | None
    dominant_project: str | None
    app_switches: int


def sustained_focus(*, start: datetime, end: datetime, min_minutes: float = 25) -> list[SustainedFocus]:
    """Find sustained active periods — any app, any mode, just continuous activity.

    Groups active intervals with max 10-minute gaps. Returns blocks >= min_minutes.
    This is the honest productivity metric: "was the person at the computer continuously?"
    """
    sessions = app_sessions(start=start, end=end)
    blocks: list[SustainedFocus] = []
    for g in group_by_gap(
        sessions, start_of=lambda s: s.start, end_of=lambda s: s.end,
        max_gap=600,  # 10 min gap max
    ):
        wall = duration_s((g.start, g.end))
        if wall / 60 < min_minutes:
            continue
        modes, projects = TopN(1), TopN(1)
        for s in g.items:
            if s.mode: modes.add(s.mode, s.duration_s)
            if s.project: projects.add(s.project, s.duration_s)
        switches = sum(1 for a, b in zip(g.items, g.items[1:]) if a.app != b.app)
        blocks.append(SustainedFocus(
            start=g.start, end=g.end, duration_min=round(wall / 60, 1),
            dominant_mode=modes.dominant, dominant_project=projects.dominant,
            app_switches=switches,
        ))
    return blocks


# ── Daily activity (composite) ──────────────────────────────────────────────


@dataclass(frozen=True)
class AWDayActivity:
    date: date
    active_hours: float
    deep_work_min: float
    fragmentation_score: float
    project_count: int
    dominant_mode: str | None
    dominant_project: str | None
    hourly_active: tuple[float, ...]  # 24 floats: active minutes per hour


def daily_activity(*, start: date, end: date) -> list[AWDayActivity]:
    """Composite daily aggregation — the AW equivalent of git.daily_activity.

    Combines active_seconds_by_date, deep_work, fragmentation, attention,
    and circadian profile into one per-day record.
    """
    s = datetime.combine(start, time.min)
    e = datetime.combine(end + timedelta(days=1), time.min)

    active_map = active_seconds_by_date(start, end)
    dw_blocks = deep_work(start=s, end=e)
    frag_data = fragmentation(start=start, end=end)
    att_data = attention(start=start, end=end)
    circ_data = circadian(start=s, end=e)
    sessions = app_sessions(start=s, end=e)

    dw_by_day: dict[date, float] = {}
    for b in dw_blocks:
        dw_by_day[b.start.date()] = dw_by_day.get(b.start.date(), 0) + b.duration_min

    frag_by_day = {f.date: f.fragmentation for f in frag_data}
    att_by_day = {a.date: a.project_count for a in att_data}

    # Hourly active minutes from circadian
    hourly: dict[date, list[float]] = {}
    for c in circ_data:
        h = hourly.setdefault(c.date, [0.0] * 24)
        h[c.hour] = c.active_min

    # Dominant mode/project from longest sessions
    mode_by_day: dict[date, str] = {}
    proj_by_day: dict[date, str] = {}
    for sess in sessions:
        d = sess.start.date()
        if d not in mode_by_day and sess.mode:
            mode_by_day[d] = sess.mode
        if d not in proj_by_day and sess.project:
            proj_by_day[d] = sess.project

    all_dates = sorted(set(active_map) | set(dw_by_day) | set(frag_by_day))
    result: list[AWDayActivity] = []
    for d in all_dates:
        if d < start or d > end:
            continue
        result.append(AWDayActivity(
            date=d,
            active_hours=round(active_map.get(d, 0) / 3600, 2),
            deep_work_min=round(dw_by_day.get(d, 0), 1),
            fragmentation_score=round(frag_by_day.get(d, 0), 3),
            project_count=att_by_day.get(d, 0),
            dominant_mode=mode_by_day.get(d),
            dominant_project=proj_by_day.get(d),
            hourly_active=tuple(hourly.get(d, [0.0] * 24)),
        ))
    return result
