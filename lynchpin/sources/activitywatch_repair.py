"""Multi-signal repair of fabricated AW AFK events.

Cohesive picture of the upstream bug
====================================

aw-watcher-afk's `not-afk` claim comes from the Wayland compositor's
`ext-idle-notifier-v1`. Hyprland decides when to fire `Idled`. For
various reasons (phantom libinput events, USB device wake cycling,
monitor DPMS cycles, autosuspend probes, idle-inhibit holds from
media-playing browsers, …) Hyprland sometimes never fires `Idled`
despite no real user activity. AW and awatcher faithfully record
what the compositor told them.

Within AW the contradiction is invisible — both buckets concur. The
repair requires EXTERNAL ground truth.

Signal hierarchy
================

Negative signals (operator was definitely AFK during this period):

  1. **Sleep records** (Samsung Health + Sleep As Android,
     2017-01-29 → 2026-03-28). Highest confidence. If a sleep
     segment overlaps a not-afk event, that overlap MUST be AFK —
     no human-input source explanation can flip it. Covers the
     ENTIRE AW history including the pre-keylog era.

  2. **Keylog silence ≥ 30 min** (scribe-tap, 2025-10-06+). Strong
     evidence. Scribe-tap reads libinput key events directly,
     bypassing the compositor. If there are no keystrokes for ≥30
     min inside a not-afk event AND no positive activity evidence
     in that sub-window, flip to AFK.

Positive activity signals (operator WAS active even if keylog/sleep
suggest otherwise):

  - **Keystrokes** in the period (≥1 keystroke ⇒ real activity).
  - **Atuin shell commands** issued in the period (operator typed
    in a terminal; not raw keystrokes but a positive activity event).

Atuin POSITIVE matters: it can lift a keylog-silence flag back to
not-afk for periods where the operator was using their shell
without keyboard input being captured (e.g., a script that ran for
a while). Atuin SILENCE does not imply AFK — most user activity
doesn't generate shell commands.

Sleep negative ALWAYS wins. We never override a sleep record with a
positive-activity signal — if Samsung Health says they were asleep,
they were asleep. Phantom keystrokes from a stuck key or pet on
keyboard don't mean the operator was active.

Provenance recording
====================

``RepairedAFKEvent.repair_source`` is one of:

  - ``""``: pass-through (no contradiction found, or no signal coverage)
  - ``"sleep-overlap"``: a sleep segment overlapped this period
  - ``"keylog-silent"``: keylog had zero presses for ≥30 min
    AND no positive activity from atuin

Pre-keylog era handling
=======================

For dates before 2025-10-06 (keylog coverage start), only sleep
records can flip not-afk to AFK. Other suspicious events (e.g.,
14h not-afk during the day) pass through — we lack ground truth.

Atuin (2025-04-03+) only helps as a positive-activity check; it
can't FLAG a period as AFK on its own.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator

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
    ``status`` is the corrected value.
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
    first_date: datetime | None
    last_date: datetime | None


def keylog_coverage() -> KeylogCoverage:
    files = log_files()
    if not files:
        return KeylogCoverage(first_date=None, last_date=None)
    dates: list[datetime] = []
    for p in files:
        try:
            d = datetime.strptime(Path(p).stem, "%Y-%m-%d")
        except ValueError:
            continue
        dates.append(d)
    if not dates:
        return KeylogCoverage(first_date=None, last_date=None)
    return KeylogCoverage(first_date=min(dates), last_date=max(dates))


@lru_cache(maxsize=1)
def _sleep_intervals() -> tuple[tuple[datetime, datetime], ...]:
    """All sleep segments across the operator's archive, sorted by start.

    Pulled once per process — sleep records change rarely (only on new
    Samsung Health export). Result is a flat sorted list of (start, end)
    intervals suitable for binary-search overlap tests.
    """
    from .sleep import entries

    intervals: list[tuple[datetime, datetime]] = []
    for entry in entries():
        for seg in entry.segments:
            intervals.append((seg.start, seg.end))
    intervals.sort()
    return tuple(intervals)


@lru_cache(maxsize=1)
def _atuin_timestamps() -> tuple[datetime, ...]:
    """All atuin command timestamps across the archive.

    Sorted list for binary-search overlap tests. Atuin coverage starts
    ~2025-04. Pre-2025-04 returns empty (no atuin signal).
    """
    try:
        from .terminal import commands
        # Pull all commands; the source iterates atuin DB once.
        # Filter to those with valid timestamps.
        from datetime import timezone
        cmds = list(commands(
            start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            end=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        ts = sorted(c.timestamp for c in cmds if c.timestamp is not None)
        return tuple(ts)
    except Exception:
        return ()


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
        has_keylog = (
            coverage.first_date is not None
            and event.end.replace(tzinfo=None) >= coverage.first_date
            and event.start.replace(tzinfo=None) <= coverage.last_date
        )
        if has_keylog:
            kp_times: list[datetime] = []
            for path in _candidate_files(event.start, event.end):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                for ts in _press_timestamps(str(path), stat.st_mtime_ns, stat.st_size):
                    if event.start <= ts <= event.end:
                        kp_times.append(ts)
            silent_windows = _find_silent_windows(
                kp_times,
                span_start=event.start,
                span_end=event.end,
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

        # Merge overlapping forced-afk windows, preserving the strongest
        # provenance. Priority order (high → low):
        #   sleep-overlap > empty-app > stuck-window > keylog-silent
        _priority = {
            "sleep-overlap": 4,
            "empty-app": 3,
            "stuck-window": 2,
            "keylog-silent": 1,
        }
        forced_afk.sort()
        merged: list[tuple[datetime, datetime, str]] = []
        for s, e, src in forced_afk:
            if merged and s <= merged[-1][1]:
                ms, me, msrc = merged[-1]
                new_src = src if _priority.get(src, 0) > _priority.get(msrc, 0) else msrc
                merged[-1] = (ms, max(me, e), new_src)
            else:
                merged.append((s, e, src))

        yield from _emit_split(event, merged)


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
