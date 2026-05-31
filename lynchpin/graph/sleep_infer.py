"""Sleep inference from wearable sleep, ActivityWatch, and keylog evidence.

The watch is the strongest signal for actual sleep, but it can produce duplicate
or partial records. ActivityWatch is useful as a bed/absence clue, but long
`not-afk` stretches can be stale when media playback prevents AFK transitions.
Keylog metadata is the counterweight: many keypresses during a watch sleep
window are a real contradiction, while no keypresses during a huge AW-active
overlap usually means AW stayed falsely active.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..core.primitives import date_to_dt_range, logical_date
from ..core.parse import as_local

__all__ = [
    "InferredSleep",
    "infer_sleep",
]


@dataclass(frozen=True)
class InferredSleep:
    """A sleep period with provenance and contradiction evidence."""
    date: date                    # logical date (6AM boundary)
    bed_start: datetime           # best available bed/rest boundary
    bed_end: datetime             # best available bed/rest boundary
    sleep_start: datetime | None  # watch-detected sleep start (None if inferred only)
    sleep_end: datetime | None    # watch-detected sleep end
    bed_duration_min: float
    sleep_duration_min: float     # watch duration, or estimated duration for aw_only
    pre_sleep_min: float
    post_sleep_min: float
    source: str                   # "watch+aw", "watch_only", "aw_only"
    sleep_score: float | None     # from watch, if available
    sleep_stages: dict[str, float] | None  # stage → minutes, if available
    confidence: float = 1.0
    evidence: tuple[str, ...] = ()
    aw_active_overlap_min: float = 0.0
    aw_active_overlap_pct: float = 0.0
    keypress_count: int = 0
    media_overlap_min: float | None = None


@dataclass(frozen=True)
class _WatchSpan:
    start: datetime
    end: datetime
    entry: object
    record_count: int = 1


def infer_sleep(
    *, start: date, end: date, min_gap_hours: float = 3.0,
    include_media: bool | None = None,
) -> list[InferredSleep]:
    """Combine watch records, AW inactivity gaps, keylog, and media evidence.

    Watch sleep is emitted even when ActivityWatch claims the computer stayed
    active. That case is flagged rather than dropped, because many real nights
    are long YouTube/music playback with no keyboard input.
    """
    from ..sources.activitywatch import active_intervals
    from ..sources.sleep import entries_in_range, sleep_architecture

    if include_media is None:
        include_media = True

    s_dt, e_dt = date_to_dt_range(start - timedelta(days=1), end + timedelta(days=1))
    active = active_intervals(start=s_dt, end=e_dt)

    active_local = sorted((as_local(a), as_local(b)) for a, b in active)
    media_intervals = _media_intervals(s_dt, e_dt) if include_media else []

    # Build inactivity gaps from AW active intervals. These are useful, but not
    # treated as authoritative sleep because the AFK watcher can stay active.
    gaps: list[tuple[datetime, datetime, float]] = []
    for i in range(len(active_local) - 1):
        _, prev_end = active_local[i]
        next_start, _ = active_local[i + 1]
        gap_h = (next_start - prev_end).total_seconds() / 3600
        if gap_h >= min_gap_hours:
            gaps.append((prev_end, next_start, gap_h))

    # Load watch sleep data
    watch_entries = list(entries_in_range(start=start - timedelta(days=1), end=end + timedelta(days=1)))
    architecture_by_date = {
        arch.date: arch
        for arch in sleep_architecture(start=start - timedelta(days=1), end=end)
    }

    raw_watch_spans: list[_WatchSpan] = []
    for e in watch_entries:
        if not e.segments or e.segments[0].start == datetime.min:
            continue
        w_start = as_local(e.segments[0].start)
        w_end = as_local(e.segments[-1].end)
        if w_end <= w_start:
            continue
        raw_watch_spans.append(_WatchSpan(w_start, w_end, e))
    watch_spans = _collapse_overlapping_watch_spans(raw_watch_spans)

    result: list[InferredSleep] = []
    used_gaps: set[int] = set()
    used_watch_intervals: list[tuple[datetime, datetime]] = []

    for watch in watch_spans:
        sleep_date = logical_date(watch.start)
        if sleep_date < start or sleep_date > end:
            continue
        used_watch_intervals.append((watch.start, watch.end))

        match_idx, match_gap = _best_gap_for_watch(watch.start, watch.end, gaps, used_gaps)
        if match_idx is not None:
            used_gaps.add(match_idx)
        if match_gap is not None:
            gap_start, gap_end, _ = match_gap
            bed_start = min(gap_start, watch.start)
            bed_end = max(gap_end, watch.end)
            source = "watch+aw"
            pre = max(0, (watch.start - bed_start).total_seconds() / 60)
            post = max(0, (bed_end - watch.end).total_seconds() / 60)
        else:
            bed_start = watch.start
            bed_end = watch.end
            source = "watch_only"
            pre = 0.0
            post = 0.0

        active_overlap = _overlap_minutes(watch.start, watch.end, active_local)
        sleep_minutes = max((watch.end - watch.start).total_seconds() / 60, 0.0)
        active_pct = (active_overlap / sleep_minutes * 100) if sleep_minutes else 0.0
        keys, keylog_state = _keypress_evidence(watch.start, watch.end)
        media_overlap = _overlap_minutes(watch.start, watch.end, media_intervals) if include_media else None

        arch = architecture_by_date.get(sleep_date)
        stages = None
        if arch is not None:
            stages = {
                "awake": arch.awake_min,
                "light": arch.light_min,
                "deep": arch.deep_min,
                "rem": arch.rem_min,
            }

        evidence = _watch_evidence(
            record_count=watch.record_count,
            sleep_minutes=sleep_minutes,
            active_pct=active_pct,
            keys=keys,
            keylog_state=keylog_state,
            media_overlap=media_overlap,
        )
        confidence = _confidence(
            base=0.9 if getattr(watch.entry, "avg_score", None) is not None else 0.75,
            evidence=evidence,
            keys=keys,
            sleep_minutes=sleep_minutes,
        )

        result.append(InferredSleep(
            date=sleep_date,
            bed_start=bed_start,
            bed_end=bed_end,
            sleep_start=watch.start,
            sleep_end=watch.end,
            bed_duration_min=round((bed_end - bed_start).total_seconds() / 60, 1),
            sleep_duration_min=round(getattr(watch.entry, "total_minutes", sleep_minutes) or sleep_minutes, 1),
            pre_sleep_min=round(pre, 1),
            post_sleep_min=round(post, 1),
            source=source,
            sleep_score=getattr(watch.entry, "avg_score", None),
            sleep_stages=stages,
            confidence=confidence,
            evidence=evidence,
            aw_active_overlap_min=round(active_overlap, 1),
            aw_active_overlap_pct=round(active_pct, 1),
            keypress_count=keys,
            media_overlap_min=round(media_overlap, 1) if media_overlap is not None else None,
        ))

    for idx, (gap_start, gap_end, gap_h) in enumerate(gaps):
        if idx in used_gaps or gap_h > 16:
            continue
        sleep_date = logical_date(gap_start)
        if sleep_date < start or sleep_date > end:
            continue
        if any(_interval_overlap_minutes(gap_start, gap_end, w_start, w_end) >= 30
               for w_start, w_end in used_watch_intervals):
            continue
        keys, keylog_state = _keypress_evidence(gap_start, gap_end)
        if keys >= 20:
            continue
        sleep_min = max(gap_h * 60 - 40, gap_h * 60 * 0.85)
        evidence = ("no_watch_sleep", "aw_inactivity_gap")
        if keylog_state == "missing":
            evidence += ("keylog_unavailable",)
        else:
            evidence += ("no_keypresses_during_gap",)
        result.append(InferredSleep(
            date=sleep_date,
            bed_start=gap_start,
            bed_end=gap_end,
            sleep_start=None,
            sleep_end=None,
            bed_duration_min=round(gap_h * 60, 1),
            sleep_duration_min=round(sleep_min, 1),
            pre_sleep_min=20.0,
            post_sleep_min=20.0,
            source="aw_only",
            sleep_score=None,
            sleep_stages=None,
            confidence=_confidence(base=0.55, evidence=evidence, keys=keys, sleep_minutes=sleep_min),
            evidence=evidence,
            keypress_count=keys,
        ))

    result.sort(key=lambda s: s.bed_start)
    return result


def _collapse_overlapping_watch_spans(spans: list[_WatchSpan]) -> list[_WatchSpan]:
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: (s.start, s.end))
    groups: list[list[_WatchSpan]] = []
    current = [ordered[0]]
    current_end = ordered[0].end
    for span in ordered[1:]:
        if span.start <= current_end:
            current.append(span)
            current_end = max(current_end, span.end)
        else:
            groups.append(current)
            current = [span]
            current_end = span.end
    groups.append(current)

    result = []
    for group in groups:
        best = max(
            group,
            key=lambda s: (
                (s.end - s.start).total_seconds(),
                1 if getattr(s.entry, "avg_score", None) is not None else 0,
            ),
        )
        result.append(_WatchSpan(best.start, best.end, best.entry, len(group)))
    return result


def _best_gap_for_watch(
    w_start: datetime, w_end: datetime, gaps: list[tuple[datetime, datetime, float]], used: set[int],
) -> tuple[int | None, tuple[datetime, datetime, float] | None]:
    best_idx = None
    best_gap = None
    best_overlap = 0.0
    watch_min = max((w_end - w_start).total_seconds() / 60, 0.0)
    for idx, (gap_start, gap_end, gap_h) in enumerate(gaps):
        if idx in used or gap_h > 16:
            continue
        overlap = _interval_overlap_minutes(w_start, w_end, gap_start, gap_end)
        near = abs((w_start - gap_start).total_seconds()) <= 90 * 60 or abs((w_end - gap_end).total_seconds()) <= 90 * 60
        if overlap > best_overlap and (overlap >= min(60, watch_min * 0.2) or near):
            best_idx = idx
            best_gap = (gap_start, gap_end, gap_h)
            best_overlap = overlap
    return best_idx, best_gap


def _interval_overlap_minutes(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> float:
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    return max((end - start).total_seconds() / 60, 0.0)


def _overlap_minutes(start: datetime, end: datetime, intervals: list[tuple[datetime, datetime]]) -> float:
    total = 0.0
    for s, e in intervals:
        if e <= start:
            continue
        if s >= end:
            break
        total += _interval_overlap_minutes(start, end, s, e)
    return total


def _keypress_evidence(start: datetime, end: datetime) -> tuple[int, str]:
    try:
        from ..sources.keylog import has_coverage, keypress_count
        covered = has_coverage(start=start, end=end)
        count = keypress_count(start=start, end=end) if covered else 0
    except Exception:
        return 0, "error"
    return count, "covered" if covered else "missing"


def _media_intervals(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    try:
        from ..sources.activitywatch import window_events
    except Exception:
        return []
    start_local = as_local(start)
    end_local = as_local(end)
    raw = list(window_events(start=start, end=end))
    if not raw:
        return []
    intervals: list[tuple[datetime, datetime]] = []
    for i, event in enumerate(raw):
        event_start = as_local(event.start)
        if i + 1 < len(raw):
            event_end = as_local(raw[i + 1].start)
        else:
            event_end = min(event_start + timedelta(seconds=5), end_local)
        if event_end <= event_start:
            continue
        app = str((event.data or {}).get("app") or "").lower()
        title = str((event.data or {}).get("title") or "").lower()
        if _is_media_window(app, title):
            intervals.append((max(event_start, start_local), min(event_end, end_local)))
    return intervals


def _is_media_window(app: str, title: str) -> bool:
    return (
        "youtube.com/watch" in title
        or "music.youtube" in title
        or " - youtube" in title
        or "spotify" in app
        or "mpv" in app
        or "vlc" in app
    )


def _watch_evidence(
    *, record_count: int, sleep_minutes: float, active_pct: float, keys: int,
    keylog_state: str, media_overlap: float | None,
) -> tuple[str, ...]:
    evidence: list[str] = ["watch_sleep"]
    if record_count > 1:
        evidence.append(f"collapsed_{record_count}_overlapping_watch_records")
    if sleep_minutes >= 14 * 60:
        evidence.append("implausibly_long_watch_sleep")
    if active_pct >= 50:
        evidence.append("aw_not_afk_during_watch_sleep")
    if keylog_state == "missing":
        evidence.append("keylog_unavailable")
    elif keys == 0 and active_pct >= 50:
        evidence.append("aw_active_probably_stale")
    elif keys >= 20:
        evidence.append("keypresses_during_watch_sleep")
    if media_overlap is not None and sleep_minutes > 0:
        media_pct = media_overlap / sleep_minutes * 100
        if media_pct >= 30 and keys <= 5:
            evidence.append("ambient_media_during_sleep")
    return tuple(evidence)


def _confidence(*, base: float, evidence: tuple[str, ...], keys: int, sleep_minutes: float) -> float:
    confidence = base
    if "collapsed_" in " ".join(evidence):
        confidence -= 0.05
    if "implausibly_long_watch_sleep" in evidence:
        confidence -= 0.25
    if "keypresses_during_watch_sleep" in evidence:
        density = keys / max(sleep_minutes, 1)
        confidence -= 0.25 if density < 0.2 else 0.45
    if "keylog_unavailable" in evidence:
        confidence -= 0.05
    if "aw_not_afk_during_watch_sleep" in evidence and "aw_active_probably_stale" not in evidence:
        confidence -= 0.1
    return round(min(max(confidence, 0.05), 0.99), 2)
