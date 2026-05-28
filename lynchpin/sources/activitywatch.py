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
import math
from collections import defaultdict
from bisect import bisect_left
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Iterable, Iterator, Sequence, TypeVar

from ..core.classify import classify
from ..core.title_features import extract_title_features
from ..core.primitives import (
    TopN,
    group_by_gap,
    merge_intervals,
    intersect_intervals,
    split_by_day,
    split_by_hour,
    duration_s,
    Interval,
)
from ..core.parse import as_local
from .activitywatch_models import (
    AWDayActivity,
    AWEvent,
    AppSession,
    AttentionMetrics,
    CircadianProfile,
    DeepWorkBlock,
    FocusLoop,
    FocusSpan,
    FocusTimelineSpan,
    FragmentationMetrics,
    ProjectFocusDay,
    SustainedFocus,
    _WindowSpan,
)
from .activitywatch_raw import afk_events, events, web_events, window_events

__all__ = [
    "AWEvent",
    "FocusSpan",
    "ProjectFocusDay",
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
    "project_focus_days",
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


# ══════════════════════════════════════════════════════════════════════════════
# Layer 1: Typed intervals
# ══════════════════════════════════════════════════════════════════════════════

_ACTIVE_STATUSES = {"not-afk", "active", "present"}
_AFK_STATUSES = {"afk", "away"}


def _repaired_afk_events(
    start: datetime, end: datetime
) -> tuple[list[Interval], list[Interval]]:
    """Return (active_clipped, afk_clipped) interval lists for [start, end).

    Pulls raw AFK events, runs them through the keylog-driven repair
    (``activitywatch_repair.repair_afk_events``) so fabricated multi-hour
    not-afk events are split around keylog-silent periods, then clips
    each segment to the requested window before merging.

    Without repair, awatcher's not-afk events that fabricate hours of
    activity (Nov 4: 28h continuous not-afk while keylog shows 23h of
    zero keystrokes) inflate downstream focused_seconds by an order of
    magnitude.
    """
    from .activitywatch_repair import repair_afk_events

    raw = list(afk_events(start=start, end=end))
    active_segs: list[Interval] = []
    afk_segs: list[Interval] = []
    for repaired in repair_afk_events(raw):
        s, e = as_local(repaired.start), as_local(repaired.end)
        # Clip to requested window AND skip segments outside it
        if e <= start or s >= end:
            continue
        clipped = (max(start, s), min(end, e))
        if repaired.status == "not-afk":
            active_segs.append(clipped)
        elif repaired.status == "afk":
            afk_segs.append(clipped)
    return merge_intervals(active_segs), merge_intervals(afk_segs)


def active_intervals(start: datetime | date, end: datetime | date) -> list[Interval]:
    """Keylog-repaired AFK-active intervals over [start, end).

    Returns the merged set of intervals during which the operator was
    genuinely active. The underlying AFK events are first repaired
    against keylog ground truth (``activitywatch_repair``) before
    clipping to the requested window — fabricated multi-hour not-afk
    claims are split around keylog-silent periods.

    If ``end`` is a date, expands to midnight of the next day.
    """
    from ..core.parse import end_of_day_local
    lower = as_local(start)
    upper = end_of_day_local(end)
    active, _ = _repaired_afk_events(lower, upper)
    return active


def afk_intervals(start: datetime | date, end: datetime | date) -> list[Interval]:
    """Keylog-repaired AFK intervals over [start, end). Mirror of
    ``active_intervals``; sees the same repair pass."""
    from ..core.parse import end_of_day_local
    lower = as_local(start)
    upper = end_of_day_local(end)
    _, afk = _repaired_afk_events(lower, upper)
    return afk


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


_FocusCountSpan = TypeVar("_FocusCountSpan", FocusSpan, FocusTimelineSpan)


def focus_spans(
    *, start: datetime | date, end: datetime | date, min_duration_s: float = 0.0
) -> list[FocusSpan]:
    """AFK-trimmed classified focus timeline.

    Accepts date or datetime. With dates, ``start`` is local midnight of
    that day and ``end`` is local midnight of the following day, giving
    the inclusive day-range a caller naturally expects when passing
    ``start=date(d), end=date(d)``.

    After classification, spans without a project are cross-referenced
    against polylogue work_events to recover session→project attribution
    for AI-coding terminal windows (kitty, foot, etc.) that the
    window-title classifier cannot resolve.
    """
    from ..core.parse import end_of_day_local
    spans = list(_focus_spans_cached(as_local(start), end_of_day_local(end), min_duration_s))
    return _enrich_with_polylogue(spans, as_local(start), end_of_day_local(end))


def _enrich_with_polylogue(
    spans: list[FocusSpan], start: datetime, end: datetime
) -> list[FocusSpan]:
    """Backfill project attribution via polylogue work_event overlap.

    When a focused span on a terminal app has no project, we check whether
    it temporally overlaps a polylogue work_event. If it does, we inherit
    the work_event's session→project mapping.

    Gracefully returns spans unchanged when polylogue is unavailable or
    its insight products are not materialized.
    """
    # Only relevant for spans that need attribution
    needy = [
        (i, s) for i, s in enumerate(spans)
        if s.kind == "focused" and not s.project
    ]
    if not needy:
        return spans

    # Lazy imports to avoid circular deps and to keep polylogue optional
    try:
        from .polylogue import work_events, session_profiles_for_date
        from .window_session_attribution import attribute_spans
    except ImportError:
        return spans

    try:
        events = work_events(start=start.date(), end=end.date())
    except Exception:
        return spans  # polylogue unavailable — spans stay as-is

    if not events:
        return spans

    # FocusSpan structurally matches SpanWindow; WorkEvent matches WorkEventWindow
    from .window_session_attribution import SpanWindow, WorkEventWindow
    from typing import cast
    needy_spans = [s for _, s in needy]
    attributions = attribute_spans(
        cast("Iterable[SpanWindow]", needy_spans),
        cast("Sequence[WorkEventWindow]", events),
    )

    # Build conversation_id → projects lookup from session profiles
    conv_projects: dict[str, tuple[str, ...]] = {}
    try:
        for profile in session_profiles_for_date(start=start.date(), end=end.date()):
            if profile.work_event_projects:
                conv_projects[profile.conversation_id] = profile.work_event_projects
    except Exception:
        pass  # session profiles unavailable — can't resolve projects

    for (idx, span), attr in zip(needy, attributions):
        if attr is None or attr.confidence < 0.3:
            continue
        projects = conv_projects.get(attr.conversation_id, ())
        if projects:
            span.project = projects[0]  # type: ignore[misc]

    return spans


def project_focus_days(*, start: datetime, end: datetime) -> list[ProjectFocusDay]:
    """Aggregate focused ActivityWatch window time by logical day and project."""
    active = active_intervals(start, end)
    totals: dict[tuple[date, str], float] = defaultdict(float)
    for window in _window_spans(
        as_local(start), as_local(end), active=active, min_duration_s=0.0
    ):
        if not window.project:
            continue
        for day, segment in split_by_day(window.start, window.end):
            totals[(day, window.project)] += duration_s(segment)
    return [
        ProjectFocusDay(date=day, project=project, duration_s=round(seconds, 3))
        for (day, project), seconds in sorted(totals.items())
        if seconds > 0
    ]


@functools.lru_cache(maxsize=16)
def _focus_spans_cached(
    start: datetime, end: datetime, min_dur: float
) -> tuple[FocusSpan, ...]:
    active = active_intervals(start, end)
    afk = afk_intervals(start, end)
    windows = _window_spans(start, end, active=active, min_duration_s=0.0)

    # Collect all boundary points
    boundaries = {start, end}
    for s, e in active:
        boundaries.add(max(s, start))
        boundaries.add(min(e, end))
    for s, e in afk:
        boundaries.add(max(s, start))
        boundaries.add(min(e, end))
    for w in windows:
        boundaries.add(max(w.start, start))
        boundaries.add(min(w.end, end))
    # Day boundaries
    cursor = datetime.combine(start.date(), time.min, tzinfo=start.tzinfo) + timedelta(
        days=1
    )
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
            spans.append(
                FocusSpan(
                    start=left,
                    end=right,
                    kind="afk",
                    app=None,
                    title=None,
                    mode=None,
                    project=None,
                )
            )
        elif (
            w_idx < len(windows)
            and windows[w_idx].start <= left
            and windows[w_idx].end >= right
        ):
            w = windows[w_idx]
            spans.append(
                FocusSpan(
                    start=left,
                    end=right,
                    kind="focused",
                    app=w.app,
                    title=w.title,
                    mode=w.mode,
                    project=w.project,
                )
            )
        elif (
            a_idx < len(active)
            and active[a_idx][0] <= left
            and active[a_idx][1] >= right
        ):
            spans.append(
                FocusSpan(
                    start=left,
                    end=right,
                    kind="active_unknown",
                    app=None,
                    title=None,
                    mode=None,
                    project=None,
                )
            )

    merged = [s for s in _merge_adjacent(spans) if s.duration_s >= min_dur]
    return tuple(_attach_keypress_counts(merged, start=start, end=end))


def focus_timeline(
    *,
    start: datetime,
    end: datetime,
    heal_afk: bool = True,
    min_duration_s: float = 0.0,
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
            if press_times
            else 0
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
                gap_spans.append(
                    FocusTimelineSpan(
                        start=gap_start,
                        end=gap_end,
                        kind="active_unknown",
                        app=None,
                        title=None,
                        mode=None,
                        project=None,
                        source="afk_gap_healed",
                    )
                )
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
            gap_spans.append(
                FocusTimelineSpan(
                    start=gap_start,
                    end=gap_end,
                    kind="coverage_gap",
                    app=None,
                    title=None,
                    mode=None,
                    project=None,
                    source="aw_afk_missing",
                )
            )

    ordered = sorted(
        [*base, *gap_spans],
        key=lambda span: (span.start, span.end, span.kind, span.source),
    )
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
    start: datetime,
    end: datetime,
    *,
    active: list[Interval] | None,
    min_duration_s: float,
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
    classification_cache: dict[
        tuple[str, str, str, str], tuple[str | None, str | None]
    ] = {}
    from .title_metadata import normalize_app
    for evt_start, evt_end, evt in timed:
        if not evt.data.get("app"):
            continue
        title = str(evt.data.get("title") or "(untitled)").strip()
        if title.lower() == "application not responding":
            continue
        # Canonicalize app (lowercase) so case-variant pairs like
        # Antigravity/antigravity collapse to one entry in downstream
        # aggregations. See `normalize_app` for rationale.
        app = normalize_app(str(evt.data["app"]))
        cwd = str(evt.data.get("cwd") or "")
        url = str(evt.data.get("url") or "")
        cache_key = (app, title, cwd, url)
        cached = classification_cache.get(cache_key)
        if cached is None:
            attr = classify(
                app=app, title=title, cwd=cwd, url=url, source="activitywatch.window"
            )
            # Enrich with title feature extraction — better project + AI detection
            feat = extract_title_features(app, title)
            project = attr.project or feat.project
            mode = attr.mode if attr.mode != "unknown" else None
            # Title features can improve mode when classify returns unknown
            if mode is None and feat.domain_category:
                mode = feat.domain_category
            if mode is None and feat.is_ai_tool:
                mode = "coding"
            cached = (mode, project)
            classification_cache[cache_key] = cached
        mode, project = cached
        if active is None:
            overlaps = [(max(evt_start, start_local), min(evt_end, end_local))]
        else:
            overlaps, iv_idx = intersect_intervals(evt_start, evt_end, active, iv_idx)
        for ov_start, ov_end in overlaps:
            for day, (seg_s, seg_e) in split_by_day(ov_start, ov_end):
                raw_spans.append(
                    _WindowSpan(
                        start=seg_s,
                        end=seg_e,
                        app=app,
                        title=title,
                        mode=mode,
                        project=project,
                    )
                )
    # Linearize merges consecutive same-app spans; filter short ones after merge
    merged = _linearize_windows(raw_spans)
    return [w for w in merged if (w.end - w.start).total_seconds() >= min_duration_s]


def _attributed_windows(
    start: datetime, end: datetime, active: list[Interval]
) -> list[_WindowSpan]:
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


def _intersect_window_spans(
    spans: Sequence[_WindowSpan], start: datetime, end: datetime
) -> list[_WindowSpan]:
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


def _keypress_timestamps(
    start: datetime, end: datetime
) -> tuple[tuple[datetime, ...], str]:
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
    press_times: Sequence[datetime],
    intervals: Sequence[Interval],
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
    spans: Sequence[_FocusCountSpan],
    *,
    start: datetime,
    end: datetime,
    keylog_state: str | None = None,
    press_times: Sequence[datetime] | None = None,
) -> list[_FocusCountSpan]:
    if not spans:
        return []
    if press_times is None or keylog_state is None:
        press_times, keylog_state = _keypress_timestamps(start, end)
    counts = (
        _count_presses_in_intervals(
            press_times, [(span.start, span.end) for span in spans]
        )
        if press_times
        else [0] * len(spans)
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
    return (
        1 if s.project else 0,
        1 if s.mode else 0,
        (s.end - s.start).total_seconds(),
    )


def _append_or_merge_win(target: list[_WindowSpan], span: _WindowSpan) -> None:
    if span.end <= span.start:
        return
    if target:
        prev = target[-1]
        if (
            prev.app == span.app
            and prev.title == span.title
            and prev.mode == span.mode
            and prev.project == span.project
            and prev.start.date() == span.start.date()
            and prev.end >= span.start
        ):
            target[-1] = replace(prev, end=max(prev.end, span.end))
            return
    target.append(span)


def _merge_adjacent(spans: Sequence[FocusSpan]) -> Iterator[FocusSpan]:
    if not spans:
        return
    current = spans[0]
    for s in spans[1:]:
        if (
            current.kind == s.kind
            and current.app == s.app
            and current.title == s.title
            and current.mode == s.mode
            and current.project == s.project
            and current.date == s.date
            and current.end >= s.start
        ):
            current = FocusSpan(
                start=current.start,
                end=max(current.end, s.end),
                kind=current.kind,
                app=current.app,
                title=current.title,
                mode=current.mode,
                project=current.project,
                keypress_count=current.keypress_count + s.keypress_count,
                keylog_state=current.keylog_state,
            )
        else:
            yield current
            current = s
    yield current


def _merge_timeline_adjacent(
    spans: Sequence[FocusTimelineSpan],
) -> Iterator[FocusTimelineSpan]:
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


def app_sessions(
    *, start: datetime, end: datetime, min_duration_s: float = 60
) -> list[AppSession]:
    spans = [
        s
        for s in focus_spans(start=start, end=end, min_duration_s=10.0)
        if s.kind == "focused" and s.app and s.title
    ]
    sessions: list[AppSession] = []
    for g in group_by_gap(
        spans,
        start_of=lambda s: s.start,
        end_of=lambda s: s.end,
        max_gap=120,
        absorb_interruption=30,
        compatible=lambda a, b: a.app == b.app and a.date == b.date,
    ):
        wall = duration_s((g.start, g.end))
        if wall < min_duration_s:
            continue
        modes, projects = TopN(1), TopN(1)
        title_dur: dict[str, float] = {}
        for s in g.items:
            d = s.duration_s
            if s.mode:
                modes.add(s.mode, d)
            if s.project:
                projects.add(s.project, d)
            title = s.title or ""
            title_dur[title] = title_dur.get(title, 0) + d
        top_title = (
            max(title_dur, key=lambda title: title_dur[title]) if title_dur else ""
        )
        sessions.append(
            AppSession(
                app=g.items[0].app or "",
                start=g.start,
                end=g.end,
                duration_s=round(wall, 3),
                title_dominant=top_title,
                titles=tuple(
                    sorted(title_dur, key=lambda title: title_dur[title], reverse=True)
                ),
                mode=modes.dominant,
                project=projects.dominant,
                interruptions=g.interruptions,
            )
        )
    return sessions


# ── Deep work blocks ──────────────────────────────────────────────────────────

_PRODUCTIVE_MODES = {"coding", "research", "writing", "planning", "chat"}


def deep_work(
    *,
    start: datetime,
    end: datetime,
    min_minutes: float = 30,
    max_interruption_ratio: float = 0.15,
) -> list[DeepWorkBlock]:
    productive = [
        s
        for s in app_sessions(start=start, end=end)
        if s.project or (s.mode or "") in _PRODUCTIVE_MODES
    ]
    blocks: list[DeepWorkBlock] = []
    for g in group_by_gap(
        productive,
        start_of=lambda s: s.start,
        end_of=lambda s: s.end,
        max_gap=600,
        absorb_interruption=300,
        compatible=_deep_compatible,
    ):
        wall = duration_s((g.start, g.end))
        productive_s = sum(s.duration_s for s in g.items)
        ratio = productive_s / wall if wall > 0 else 0
        if wall / 60 >= min_minutes and ratio >= (1 - max_interruption_ratio):
            modes, projects = TopN(1), TopN(1)
            for s in g.items:
                if s.mode:
                    modes.add(s.mode, s.duration_s)
                if s.project:
                    projects.add(s.project, s.duration_s)
            switches = sum(1 for a, b in zip(g.items, g.items[1:]) if a.app != b.app)
            blocks.append(
                DeepWorkBlock(
                    start=g.start,
                    end=g.end,
                    duration_min=round(wall / 60, 1),
                    project=projects.dominant,
                    mode=modes.dominant or "unknown",
                    focus_ratio=round(ratio, 3),
                    app_switches=switches,
                )
            )
    return blocks


def _deep_compatible(a: AppSession, b: AppSession) -> bool:
    if a.project and b.project:
        return a.project == b.project
    if a.mode and b.mode:
        return a.mode == b.mode and a.mode in _PRODUCTIVE_MODES
    return False


# ── Circadian profiles ────────────────────────────────────────────────────────


def circadian(*, start: date, end: date) -> list[CircadianProfile]:
    s = datetime.combine(start, time.min)
    e = datetime.combine(end + timedelta(days=1), time.min)
    buckets: dict[tuple[date, int], tuple[TopN, TopN, float, float]] = {}
    for span in focus_spans(start=s, end=e, min_duration_s=30):
        for hour, seg in split_by_hour(span.start, span.end):
            key = (span.date, hour)
            modes, projects, active, recovery = buckets.get(
                key, (TopN(1), TopN(1), 0.0, 0.0)
            )
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
    result: list[CircadianProfile] = []
    for (d, h), (modes, projects, active, recovery) in sorted(buckets.items()):
        if active > 0 or recovery > 0:
            result.append(
                CircadianProfile(
                    d,
                    h,
                    round(active, 1),
                    round(recovery, 1),
                    modes.dominant,
                    projects.dominant,
                )
            )
    return result


# ── Focus loops (A↔B alternation) ────────────────────────────────────────────


def loops(
    *, start: datetime, end: datetime, min_spans: int = 4, max_gap: float = 180
) -> list[FocusLoop]:
    spans = [
        s for s in focus_spans(start=start, end=end) if s.kind == "focused" and s.app
    ]
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
                i += 1
                continue
            if (second.start - first.end).total_seconds() > max_gap:
                i += 1
                continue
            ctx_a, ctx_b = _ctx(first), _ctx(second)
            collected = [first, second]
            expected = ctx_a
            j = i + 2
            while j < len(ds):
                span = ds[j]
                if (span.start - collected[-1].end).total_seconds() > max_gap:
                    break
                ctx = _ctx(span)
                if ctx not in {ctx_a, ctx_b} or ctx != expected:
                    break
                collected.append(span)
                expected = ctx_b if expected == ctx_a else ctx_a
                j += 1
            if len(collected) >= min_spans:
                dur = (collected[-1].end - collected[0].start).total_seconds() / 60
                if dur >= 8:
                    projects = TopN(1)
                    for s in collected:
                        if s.project:
                            projects.add(s.project, s.duration_s)
                    result.append(
                        FocusLoop(
                            date=day,
                            start=collected[0].start,
                            end=collected[-1].end,
                            duration_min=round(dur, 1),
                            span_count=len(collected),
                            switch_count=len(collected) - 1,
                            context_a=f"{first.app}::{first.title}",
                            context_b=f"{second.app}::{second.title}",
                            dominant_project=projects.dominant,
                        )
                    )
                    i = j
                    continue
            i += 1
    return result


def _ctx(s: FocusSpan) -> tuple[str | None, str | None]:
    return (s.app, s.title)


# ── Fragmentation ─────────────────────────────────────────────────────────────


def fragmentation(*, start: date, end: date) -> list[FragmentationMetrics]:
    s = datetime.combine(start, time.min)
    e = datetime.combine(end + timedelta(days=1), time.min)
    sessions = app_sessions(start=s, end=e)
    by_day: dict[date, list[AppSession]] = {}
    for sess in sessions:
        by_day.setdefault(sess.start.date(), []).append(sess)

    result: list[FragmentationMetrics] = []
    for day, ds in sorted(by_day.items()):
        if len(ds) < 2:
            continue
        stretches = _focus_stretches(ds)
        if not stretches:
            continue
        longest = max(stretches)
        total = sum(stretches)
        result.append(
            FragmentationMetrics(
                date=day,
                total_switches=len(ds) - 1,
                avg_focus_min=round(total / len(stretches), 1),
                longest_focus_min=round(longest, 1),
                fragmentation=round(max(0, min(1, 1 - longest / total)), 3)
                if total > 0
                else 0,
            )
        )
    return result


def _focus_stretches(sessions: Sequence[AppSession]) -> list[float]:
    if not sessions:
        return []
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
    if s.project:
        return f"project:{s.project}"
    if s.mode:
        return f"mode:{s.mode}"
    return f"app:{s.app}"


# ── Project attention ─────────────────────────────────────────────────────────


def attention(*, start: date, end: date) -> list[AttentionMetrics]:
    s = datetime.combine(start, time.min)
    e = datetime.combine(end + timedelta(days=1), time.min)
    spans = focus_spans(start=s, end=e, min_duration_s=60)
    by_day: dict[date, dict[str, float]] = {}
    for span in spans:
        if span.kind != "focused" or not span.project:
            continue
        bucket = by_day.setdefault(span.date, {})
        bucket[span.project] = bucket.get(span.project, 0) + span.duration_s

    result: list[AttentionMetrics] = []
    for day, projects in sorted(by_day.items()):
        if not projects:
            continue
        total = sum(projects.values())
        probs = [v / total for v in projects.values()]
        entropy = -sum(p * math.log2(p) for p in probs if p > 0)
        sorted_vals = sorted(projects.values())
        n = len(sorted_vals)
        gini = (
            (
                2 * sum((i + 1) * v for i, v in enumerate(sorted_vals)) / (n * total)
                - (n + 1) / n
            )
            if n > 0 and total > 0
            else 0
        )
        top = max(projects, key=lambda project: projects[project])
        result.append(
            AttentionMetrics(
                date=day,
                entropy=round(entropy, 3),
                gini=round(gini, 3),
                top_project=top,
                project_count=n,
            )
        )
    return result


# ── Sustained focus (mode-agnostic) ──────────────────────────────────────────


def sustained_focus(
    *, start: datetime, end: datetime, min_minutes: float = 25
) -> list[SustainedFocus]:
    """Find sustained active periods — any app, any mode, just continuous activity.

    Groups active intervals with max 10-minute gaps. Returns blocks >= min_minutes.
    This is the honest productivity metric: "was the person at the computer continuously?"
    """
    sessions = app_sessions(start=start, end=end)
    blocks: list[SustainedFocus] = []
    for g in group_by_gap(
        sessions,
        start_of=lambda s: s.start,
        end_of=lambda s: s.end,
        max_gap=600,  # 10 min gap max
    ):
        wall = duration_s((g.start, g.end))
        if wall / 60 < min_minutes:
            continue
        modes, projects = TopN(1), TopN(1)
        for s in g.items:
            if s.mode:
                modes.add(s.mode, s.duration_s)
            if s.project:
                projects.add(s.project, s.duration_s)
        switches = sum(1 for a, b in zip(g.items, g.items[1:]) if a.app != b.app)
        blocks.append(
            SustainedFocus(
                start=g.start,
                end=g.end,
                duration_min=round(wall / 60, 1),
                dominant_mode=modes.dominant,
                dominant_project=projects.dominant,
                app_switches=switches,
            )
        )
    return blocks


# ── Daily activity (composite) ──────────────────────────────────────────────


def daily_activity(*, start: date, end: date) -> list[AWDayActivity]:
    """Composite daily aggregation — the AW equivalent of git.daily_activity.

    Combines active_seconds_by_date, deep_work, fragmentation, attention,
    circadian profile, and AW outage detection into one per-day record.

    ``outage_hours`` reports hours where AW data was unavailable due to
    watcher/daemon downtime rather than operator AFK. Days with high
    outage_hours should not be interpreted as low-activity days.
    """
    s = datetime.combine(start, time.min)
    e = datetime.combine(end + timedelta(days=1), time.min)

    active_map = active_seconds_by_date(start, end)
    dw_blocks = deep_work(start=s, end=e)
    frag_data = fragmentation(start=start, end=end)
    att_data = attention(start=start, end=end)
    circ_data = circadian(start=s, end=e)
    sessions = app_sessions(start=s, end=e)
    outage_map = _daily_outage_hours(start=s, end=e)

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

    # Cross-source presence (keylog + AW, resilience against AW outages)
    presence_map = _daily_presence_summary(start=start, end=end)

    all_dates = sorted(
        set(active_map) | set(dw_by_day) | set(frag_by_day)
        | set(outage_map) | set(presence_map)
    )
    result: list[AWDayActivity] = []
    for d in all_dates:
        if d < start or d > end:
            continue
        pres = presence_map.get(d, {})
        result.append(
            AWDayActivity(
                date=d,
                active_hours=round(active_map.get(d, 0) / 3600, 2),
                deep_work_min=round(dw_by_day.get(d, 0), 1),
                fragmentation_score=round(frag_by_day.get(d, 0), 3),
                project_count=att_by_day.get(d, 0),
                dominant_mode=mode_by_day.get(d),
                dominant_project=proj_by_day.get(d),
                hourly_active=tuple(hourly.get(d, [0.0] * 24)),
                outage_hours=round(outage_map.get(d, 0) / 3600, 2),
                presence_active_hours=round(pres.get("active_hours", 0), 2),
                presence_typing_hours=round(pres.get("typing_hours", 0), 2),
                presence_data_gap_hours=round(pres.get("data_gap_hours", 0), 2),
            )
        )
    return result


def _daily_presence_summary(
    *, start: date, end: date
) -> dict[date, dict[str, float]]:
    """Compute cross-source presence summary per day from hourly_presence()."""
    try:
        from .presence import hourly_presence
        hours = list(hourly_presence(start, end))
    except Exception:
        return {}

    by_day: dict[date, dict[str, float]] = defaultdict(
        lambda: {"active_hours": 0, "typing_hours": 0, "data_gap_hours": 0}
    )
    for h in hours:
        d = h.hour_utc.date()
        if h.derived_state in ("active_typing", "active_no_typing"):
            by_day[d]["active_hours"] += 1
        if h.derived_state == "active_typing":
            by_day[d]["typing_hours"] += 1
        if h.derived_state == "data_gap":
            by_day[d]["data_gap_hours"] += 1
    return dict(by_day)


def _daily_outage_hours(*, start: datetime, end: datetime) -> dict[date, float]:
    """Compute per-day AW outage seconds from cross-bucket gap detection."""
    try:
        from .activitywatch_outages import detect_data_outages
        outages = detect_data_outages(start=start, end=end)
    except Exception:
        return {}

    by_day: dict[date, float] = defaultdict(float)
    for outage in outages:
        d = outage.start.date()
        # Only count patterns A (full outage) and B (awatcher process died)
        # Pattern C (afk-only silent) is tricky — window+web still work,
        # so it's partial. Count at 50% weight.
        weight = 1.0 if outage.pattern in ("A", "B") else 0.5
        by_day[d] += outage.duration_s * weight
    return dict(by_day)
