"""Derive AFK intervals from ActivityWatch and independent source evidence.

ActivityWatch reports the compositor's idle state. This repair keeps that raw
claim intact and emits separately labelled inferences from overlapping sleep,
empty-app, stuck-window, and keylog signals. A missing keylog file is missing
coverage, not evidence of silence. A label applies only to the segment its
source supports; merging adjacent AFK time must not extend its provenance.
Sleep takes precedence on an actual overlap, but that precedence is a product
policy rather than proof that a conflicting observation is false.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator

from ..core.cache import file_signature
from ..core.config import get_config
from .activitywatch_models import AWEvent
from .activitywatch_raw import window_events
from .keylog import _candidate_files, _press_timestamps, log_files

__all__ = [
    "KEYLOG_SILENT_THRESHOLD_S",
    "STUCK_WINDOW_THRESHOLD_S",
    "KeylogCoverage",
    "RepairedAFKEvent",
    "repair_afk_events",
    "keylog_coverage",
]


# Keylog-silent threshold: 30 min. Above awatcher's idle-timeout (60s);
# below the multi-hour fabrications we're catching; above ordinary
# reading-without-typing.
KEYLOG_SILENT_THRESHOLD_S: float = 30 * 60

# Stuck-window threshold: 6 hours. Beyond any plausible human attention
# span on a single window. Pre-keylog data has fabrications like 23h on
# a single LessWrong article or 18h on a ChatGPT tab — the operator was
# clearly asleep or away while heartbeat-merge kept the event open.
STUCK_WINDOW_THRESHOLD_S: float = 6 * 3600


@dataclass(frozen=True)
class RepairedAFKEvent:
    """An AFK event after multi-signal repair.

    ``original_status`` is the unmodified AW claim.
    ``status`` is the selected derived value, not a rewrite of either source.
    ``repair_source``:
      - ``""``: pass-through
      - ``"sleep-overlap"``: sleep record overlapped this period
      - ``"keylog-silent"``: keylog silence + atuin silence
    """
    bucket: str
    start: datetime
    end: datetime
    status: str
    original_status: str
    repair_source: str

    @property
    def repaired(self) -> bool:
        return self.repair_source != ""


@dataclass(frozen=True)
class KeylogCoverage:
    days: frozenset[date]


def keylog_coverage() -> KeylogCoverage:
    files = log_files()
    if not files:
        return KeylogCoverage(days=frozenset())
    days: set[date] = set()
    for p in files:
        try:
            day = date.fromisoformat(Path(p).stem)
        except ValueError:
            continue
        days.add(day)
    return KeylogCoverage(days=frozenset(days))


def repair_input_revision() -> tuple[object, ...]:
    """Return signatures for the external signals used by AFK repair."""
    cfg = get_config()
    keylog_root = cfg.keylog_root / "logs"
    keylog_files = sorted(keylog_root.glob("*.jsonl")) if keylog_root.exists() else []
    atuin_path = cfg.data_root / "activity/terminal/shell/atuin/history.ndjson"
    atuin_db = getattr(cfg, "atuin_db", Path())
    paths = [
        cfg.sleep_jsonl,
        atuin_path,
        atuin_db,
        *keylog_files,
    ]
    return tuple(file_signature(path) for path in paths)


def _sleep_source_signature() -> object:
    return file_signature(get_config().sleep_jsonl)


def _atuin_source_signature() -> tuple[object, ...]:
    cfg = get_config()
    atuin_db = getattr(cfg, "atuin_db", Path())
    return (
        file_signature(cfg.data_root / "activity/terminal/shell/atuin/history.ndjson"),
        file_signature(atuin_db),
    )


@lru_cache(maxsize=4)
def _sleep_intervals_cached(
    signature: object,
) -> tuple[tuple[datetime, datetime], ...]:
    del signature
    from .sleep import entries

    intervals: list[tuple[datetime, datetime]] = []
    for entry in entries():
        for seg in entry.segments:
            intervals.append((seg.start, seg.end))
    intervals.sort()
    return tuple(intervals)


def _sleep_intervals() -> tuple[tuple[datetime, datetime], ...]:
    """All sleep segments across the operator's archive, sorted by start.

    Cached by the sleep source signature. The result is a flat sorted list of
    (start, end) intervals suitable for binary-search overlap tests.
    """
    return _sleep_intervals_cached(_sleep_source_signature())


@lru_cache(maxsize=4)
def _atuin_timestamps_cached(signature: object) -> tuple[datetime, ...]:
    del signature
    try:
        from .terminal import commands
        from datetime import timezone

        cmds = list(commands(
            start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            end=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        return tuple(sorted(c.timestamp for c in cmds if c.timestamp is not None))
    except Exception:
        return ()


def _atuin_timestamps() -> tuple[datetime, ...]:
    """All atuin command timestamps across the archive.

    Sorted list for binary-search overlap tests. Atuin coverage starts
    ~2025-04. Pre-2025-04 returns empty (no atuin signal).
    """
    return _atuin_timestamps_cached(_atuin_source_signature())


def _overlapping_sleep(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Return sleep intervals that overlap [start, end], clipped to that range."""
    all_sleep = _sleep_intervals()
    if not all_sleep:
        return []
    # Binary-search lower bound
    lo = bisect.bisect_left(all_sleep, (start - timedelta(days=1), start - timedelta(days=1)))
    overlaps: list[tuple[datetime, datetime]] = []
    for i in range(lo, len(all_sleep)):
        s, e = all_sleep[i]
        if s >= end:
            break
        if e <= start:
            continue
        overlaps.append((max(s, start), min(e, end)))
    return overlaps


def _atuin_in_window(start: datetime, end: datetime) -> bool:
    """True iff at least one atuin command timestamp falls in [start, end)."""
    ts = _atuin_timestamps()
    if not ts:
        return False
    lo = bisect.bisect_left(ts, start)
    return lo < len(ts) and ts[lo] < end


def _find_silent_windows(
    timestamps: list[datetime],
    *,
    span_start: datetime,
    span_end: datetime,
    threshold_s: float,
) -> list[tuple[datetime, datetime]]:
    """Gaps ≥ threshold_s in a sorted point-event sequence."""
    if not timestamps:
        gap = (span_end - span_start).total_seconds()
        return [(span_start, span_end)] if gap >= threshold_s else []
    boundaries = [span_start, *sorted(timestamps), span_end]
    return [
        (left, right)
        for left, right in zip(boundaries, boundaries[1:])
        if (right - left).total_seconds() >= threshold_s
    ]


def _covered_keylog_spans(
    start: datetime, end: datetime, days: frozenset[date]
) -> Iterator[tuple[datetime, datetime]]:
    """Yield only spans backed by an actual UTC-dated keylog file.

    A first/last date range does not establish capture on a missing day.  We
    also avoid carrying an inferred silence gap across a file boundary.
    """
    cursor = start.astimezone(UTC)
    upper = end.astimezone(UTC)
    while cursor < upper:
        next_day = datetime.combine(cursor.date() + timedelta(days=1), time.min, tzinfo=UTC)
        span_end = min(upper, next_day)
        if cursor.date() in days:
            yield cursor.astimezone(start.tzinfo), span_end.astimezone(start.tzinfo)
        cursor = span_end


def _labelled_afk_segments(
    intervals: list[tuple[datetime, datetime, str]],
) -> list[tuple[datetime, datetime, str]]:
    """Partition overlapping inferences without lending one label to a union."""
    priority = {"sleep-overlap": 4, "empty-app": 3, "stuck-window": 2, "keylog-silent": 1}
    boundaries = sorted({point for start, end, _ in intervals for point in (start, end)})
    labelled: list[tuple[datetime, datetime, str]] = []
    for start, end in zip(boundaries, boundaries[1:]):
        sources = (source for left, right, source in intervals if left < end and right > start)
        source = max(sources, key=lambda item: priority.get(item, 0), default="")
        if not source:
            continue
        if labelled and labelled[-1][1] == start and labelled[-1][2] == source:
            labelled[-1] = (labelled[-1][0], end, source)
        else:
            labelled.append((start, end, source))
    return labelled


def repair_afk_events(
    events: Iterable[AWEvent],
    *,
    keylog_silent_threshold_s: float = KEYLOG_SILENT_THRESHOLD_S,
    stuck_window_threshold_s: float = STUCK_WINDOW_THRESHOLD_S,
) -> Iterator[RepairedAFKEvent]:
    """Yield AFK events with multi-signal repair applied.

    Signal hierarchy (highest confidence first):
      1. Sleep-overlap: any segment from sleep entries inside the
         not-afk event → AFK (covers entire AW history)
      2. Empty-app window: window watcher emitted events with empty
         ``app`` field (lock screen / no-focus state) → AFK
      3. Stuck-window: a single window event with duration ≥ 6h via
         heartbeat-merge (no focus changes for 6+ hours) → AFK.
         Combined with the absence of atuin commands during the
         stretch — humans don't stare at one page for 6+ hours.
      4. Keylog-silent: where keylog covers, gaps ≥ 30 min with no
         atuin commands → AFK
    """
    coverage = keylog_coverage()

    for event in events:
        status = str((event.data or {}).get("status") or "").strip().lower()
        if status != "not-afk":
            yield RepairedAFKEvent(
                bucket=event.bucket, start=event.start, end=event.end,
                status=status or "unknown",
                original_status=status or "unknown",
                repair_source="",
            )
            continue

        # === Signal 1: sleep overlap ===
        forced_afk: list[tuple[datetime, datetime, str]] = []
        sleep_overlaps = _overlapping_sleep(event.start, event.end)
        for s, e in sleep_overlaps:
            forced_afk.append((s, e, "sleep-overlap"))

        # === Signal 2 & 3: query window events once for both signals ===
        # Gate: only run the (expensive) window-events lookup when the
        # not-afk event is long enough to plausibly contain a stuck-window
        # or substantial empty-app stretch. Saves orders of magnitude in
        # the common case (most not-afk events are < 30 min).
        wnd_events = (
            list(window_events(start=event.start, end=event.end))
            if (event.end - event.start).total_seconds() >= 60 * 60
            else []
        )

        # Signal 2: empty-app windows = lock screen / no-focus state
        for w in wnd_events:
            data = w.data or {}
            app = str(data.get("app") or "").strip()
            if not app:
                # Clip to event window
                s_c = max(w.start, event.start)
                e_c = min(w.end, event.end)
                if e_c > s_c:
                    forced_afk.append((s_c, e_c, "empty-app"))

        # Signal 3: stuck-window = single window event ≥ 6h with no
        # atuin commands during it (no shell activity → operator not there).
        for w in wnd_events:
            dur_s = (w.end - w.start).total_seconds()
            if dur_s < stuck_window_threshold_s:
                continue
            s_c = max(w.start, event.start)
            e_c = min(w.end, event.end)
            if e_c <= s_c:
                continue
            # Positive-activity rescue: atuin command during this stretch
            # implies real activity even if no focus changes (e.g., long
            # build running in background).
            if _atuin_in_window(s_c, e_c):
                continue
            forced_afk.append((s_c, e_c, "stuck-window"))

        # === Signal 4: keylog silence (where keylog covers) ===
        keylog_spans = tuple(_covered_keylog_spans(event.start, event.end, coverage.days))
        if keylog_spans:
            kp_times: list[datetime] = []
            for path in _candidate_files(event.start, event.end):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                for ts in _press_timestamps(str(path), stat.st_mtime_ns, stat.st_size):
                    if event.start <= ts <= event.end:
                        kp_times.append(ts)
            for span_start, span_end in keylog_spans:
                silent_windows = _find_silent_windows(
                    [ts for ts in kp_times if span_start <= ts <= span_end],
                    span_start=span_start,
                    span_end=span_end,
                    threshold_s=keylog_silent_threshold_s,
                )
                for s, e in silent_windows:
                    if _is_covered_by_sleep(s, e, sleep_overlaps):
                        continue
                    if _atuin_in_window(s, e):
                        continue
                    forced_afk.append((s, e, "keylog-silent"))

        if not forced_afk:
            yield RepairedAFKEvent(
                bucket=event.bucket, start=event.start, end=event.end,
                status="not-afk", original_status="not-afk",
                repair_source="",
            )
            continue

        yield from _emit_split(event, _labelled_afk_segments(forced_afk))


def _is_covered_by_sleep(
    start: datetime,
    end: datetime,
    sleep_overlaps: list[tuple[datetime, datetime]],
) -> bool:
    """True if [start, end] is fully contained in any sleep overlap."""
    for s, e in sleep_overlaps:
        if s <= start and e >= end:
            return True
    return False


def _emit_split(
    event: AWEvent,
    forced_afk: list[tuple[datetime, datetime, str]],
) -> Iterator[RepairedAFKEvent]:
    """Emit alternating not-afk / AFK segments around the forced AFK windows."""
    cursor = event.start
    for s_start, s_end, src in forced_afk:
        if s_start > cursor:
            yield RepairedAFKEvent(
                bucket=event.bucket, start=cursor, end=s_start,
                status="not-afk", original_status="not-afk",
                repair_source="",
            )
        yield RepairedAFKEvent(
            bucket=event.bucket, start=s_start, end=s_end,
            status="afk", original_status="not-afk",
            repair_source=src,
        )
        cursor = s_end
    if cursor < event.end:
        yield RepairedAFKEvent(
            bucket=event.bucket, start=cursor, end=event.end,
            status="not-afk", original_status="not-afk",
            repair_source="",
        )
