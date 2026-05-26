"""Tests for the multi-signal AFK repair.

Signal priority: sleep-overlap (highest, covers all years) → keylog-
silent (where keylog covers, 2025-10+). Atuin commands are a positive
activity signal that can RESCUE a keylog-silent window from being
flipped to AFK.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from lynchpin.sources.activitywatch_models import AWEvent
from lynchpin.sources.activitywatch_repair import (
    KEYLOG_SILENT_THRESHOLD_S,
    KeylogCoverage,
    repair_afk_events,
)

UTC = timezone.utc
BASE = datetime(2026, 5, 26, 10, tzinfo=UTC)


def _aw(status: str, start_s: int, end_s: int) -> AWEvent:
    return AWEvent(
        bucket="aw-watcher-afk_host",
        start=BASE + timedelta(seconds=start_s),
        end=BASE + timedelta(seconds=end_s),
        data={"status": status},
    )


def _patch_signals(
    *,
    keypress_seconds=(),
    atuin_seconds=(),
    sleep_intervals_s=(),
    window_events_spec=(),  # list of (start_s, end_s, {data dict})
    keylog_covered: bool = True,
):
    """Mock all the signal sources used by the repair."""
    ts_tuple = tuple(BASE + timedelta(seconds=s) for s in keypress_seconds)
    atuin_tuple = tuple(BASE + timedelta(seconds=s) for s in atuin_seconds)
    sleep_tuple = tuple(
        (BASE + timedelta(seconds=s), BASE + timedelta(seconds=e))
        for s, e in sleep_intervals_s
    )
    window_objs = [
        SimpleNamespace(
            start=BASE + timedelta(seconds=s),
            end=BASE + timedelta(seconds=e),
            data=d,
        ) for s, e, d in window_events_spec
    ]

    fake_path = SimpleNamespace(stat=lambda: SimpleNamespace(st_mtime_ns=0, st_size=0))

    coverage = (
        KeylogCoverage(first_date=datetime(2020, 1, 1), last_date=datetime(2030, 1, 1))
        if keylog_covered
        else KeylogCoverage(first_date=None, last_date=None)
    )

    def mock_window_events(*, start, end):
        return [w for w in window_objs if w.start < end and w.end > start]

    return [
        patch(
            "lynchpin.sources.activitywatch_repair._candidate_files",
            return_value=[fake_path],
        ),
        patch(
            "lynchpin.sources.activitywatch_repair._press_timestamps",
            return_value=ts_tuple,
        ),
        patch(
            "lynchpin.sources.activitywatch_repair.keylog_coverage",
            return_value=coverage,
        ),
        patch(
            "lynchpin.sources.activitywatch_repair._atuin_timestamps",
            return_value=atuin_tuple,
        ),
        patch(
            "lynchpin.sources.activitywatch_repair._sleep_intervals",
            return_value=sleep_tuple,
        ),
        patch(
            "lynchpin.sources.activitywatch_repair.window_events",
            side_effect=mock_window_events,
        ),
    ]


def _enter(ctxs):
    return [c.__enter__() for c in ctxs]


def _exit(ctxs):
    for c in reversed(ctxs):
        c.__exit__(None, None, None)


# ── Sleep-overlap repair (highest priority) ──────────────────────────


def test_sleep_overlap_flips_to_afk() -> None:
    """A sleep segment inside a not-afk event → that overlap is AFK."""
    events = [_aw("not-afk", 0, 10 * 3600)]
    # Sleep from t+1h to t+8h (inside the not-afk event).
    # Keystrokes throughout to isolate the sleep signal (otherwise the
    # whole span would also fail the keylog-silent check and merge).
    ctxs = _patch_signals(
        sleep_intervals_s=[(3600, 8 * 3600)],
        keypress_seconds=list(range(0, 10 * 3600, 60)),  # 1 keystroke/min
    )
    _enter(ctxs)
    try:
        out = list(repair_afk_events(events))
    finally:
        _exit(ctxs)
    sleep_flips = [e for e in out if e.repair_source == "sleep-overlap"]
    assert len(sleep_flips) == 1
    assert (sleep_flips[0].end - sleep_flips[0].start).total_seconds() == 7 * 3600


def test_sleep_overlap_works_pre_keylog_era() -> None:
    """The most important coverage: dates before 2025-10-06 (no keylog).
    Sleep records still flip not-afk → AFK."""
    events = [_aw("not-afk", 0, 8 * 3600)]
    ctxs = _patch_signals(
        sleep_intervals_s=[(3600, 7 * 3600)],
        keylog_covered=False,
    )
    _enter(ctxs)
    try:
        out = list(repair_afk_events(events))
    finally:
        _exit(ctxs)
    sleep_flips = [e for e in out if e.repair_source == "sleep-overlap"]
    assert len(sleep_flips) == 1


def test_sleep_overlap_beats_phantom_keystrokes() -> None:
    """If keylog records phantom presses during a sleep period (stuck key,
    cat on keyboard, …), sleep wins. Operator was asleep."""
    events = [_aw("not-afk", 0, 8 * 3600)]
    # Keystrokes every minute throughout
    ctxs = _patch_signals(
        keypress_seconds=list(range(0, 8 * 3600, 60)),
        sleep_intervals_s=[(2 * 3600, 6 * 3600)],
    )
    _enter(ctxs)
    try:
        out = list(repair_afk_events(events))
    finally:
        _exit(ctxs)
    # The 4h sleep window must show up as AFK
    sleep_flips = [e for e in out if e.repair_source == "sleep-overlap"]
    assert sleep_flips
    assert (sleep_flips[0].end - sleep_flips[0].start).total_seconds() == 4 * 3600


# ── Keylog-silent + atuin rescue ────────────────────────────────────


def test_keylog_silent_with_no_atuin_flips() -> None:
    """30-min keylog silence + no atuin commands → AFK."""
    events = [_aw("not-afk", 0, 2 * 3600)]
    # Keystrokes only at start and end
    ctxs = _patch_signals(keypress_seconds=[0, 7000])
    _enter(ctxs)
    try:
        out = list(repair_afk_events(events))
    finally:
        _exit(ctxs)
    # 2h event, keys at 0s and 7000s ≈ 1h56m, so first ~1h56m is silent (>30m).
    # But wait: keys appear so the "silent" gap is 0 → 7000s (≥ 30m), that's
    # the AFK window. Then a tiny not-afk at the end.
    repaired = [e for e in out if e.repaired]
    assert repaired
    assert all(e.repair_source == "keylog-silent" for e in repaired)


def test_atuin_command_rescues_keylog_silent_window() -> None:
    """Operator was running a long shell command — no keystrokes for 1h
    but atuin recorded the command. Period stays not-afk."""
    events = [_aw("not-afk", 0, 2 * 3600)]
    # No keystrokes at all → entire 2h would be keylog-silent.
    # But atuin command at 1h mark = positive activity.
    ctxs = _patch_signals(atuin_seconds=[3600])
    _enter(ctxs)
    try:
        out = list(repair_afk_events(events))
    finally:
        _exit(ctxs)
    # Atuin command rescues the whole silent window → no AFK flip.
    repaired = [e for e in out if e.repaired]
    assert not repaired


def test_no_signal_pass_through() -> None:
    """No sleep, no keylog coverage, no atuin → must pass through
    unchanged. Pre-keylog era with no overlapping sleep record."""
    events = [_aw("not-afk", 0, 6 * 3600)]
    ctxs = _patch_signals(keylog_covered=False)
    _enter(ctxs)
    try:
        out = list(repair_afk_events(events))
    finally:
        _exit(ctxs)
    assert len(out) == 1
    assert out[0].repair_source == ""


# ── Combined / edge cases ────────────────────────────────────────────


def test_sleep_and_keylog_both_apply_priority_kept() -> None:
    """When sleep AND keylog-silent both want to flag the same window,
    the merged interval keeps the sleep-overlap provenance (higher conf)."""
    events = [_aw("not-afk", 0, 10 * 3600)]
    # Sleep 1h-7h, keylog silent throughout
    ctxs = _patch_signals(sleep_intervals_s=[(3600, 7 * 3600)])
    _enter(ctxs)
    try:
        out = list(repair_afk_events(events))
    finally:
        _exit(ctxs)
    repaired = [e for e in out if e.repaired]
    sources = {e.repair_source for e in repaired}
    # At minimum sleep-overlap is the source for the central window
    assert "sleep-overlap" in sources


def test_afk_event_passes_through() -> None:
    events = [_aw("afk", 0, 600)]
    ctxs = _patch_signals()
    _enter(ctxs)
    try:
        out = list(repair_afk_events(events))
    finally:
        _exit(ctxs)
    assert len(out) == 1
    assert out[0].status == "afk"
    assert not out[0].repaired


def test_segments_cover_original_exactly() -> None:
    events = [_aw("not-afk", 0, 6 * 3600)]
    ctxs = _patch_signals(sleep_intervals_s=[(3600, 4 * 3600)])
    _enter(ctxs)
    try:
        out = list(repair_afk_events(events))
    finally:
        _exit(ctxs)
    assert out[0].start == events[0].start
    assert out[-1].end == events[0].end
    for prev, curr in zip(out, out[1:]):
        assert prev.end == curr.start


def test_empty_app_window_flips_to_afk() -> None:
    """When the window watcher emits empty-app events during a not-afk
    period (lock screen / no-focus state), that window is AFK."""
    events = [_aw("not-afk", 0, 4 * 3600)]
    ctxs = _patch_signals(
        # 2h of lock-screen (empty app) in the middle
        window_events_spec=[
            (0, 3600, {"app": "kitty", "title": "work"}),
            (3600, 3 * 3600, {"app": "", "title": ""}),  # lock screen
            (3 * 3600, 4 * 3600, {"app": "kitty", "title": "work resume"}),
        ],
        keypress_seconds=list(range(0, 4 * 3600, 30)),
    )
    _enter(ctxs)
    try:
        out = list(repair_afk_events(events))
    finally:
        _exit(ctxs)
    empty_app = [e for e in out if e.repair_source == "empty-app"]
    assert empty_app
    assert (empty_app[0].end - empty_app[0].start).total_seconds() == 2 * 3600


def test_stuck_window_above_threshold_flips_to_afk() -> None:
    """A single window event lasting 8h via heartbeat-merge (no focus
    changes) → AFK. The Nov-4 pattern: operator left Chrome on a
    LessWrong tab for 23h."""
    events = [_aw("not-afk", 0, 10 * 3600)]
    ctxs = _patch_signals(
        # ONE 8-hour window event spanning most of the not-afk
        window_events_spec=[
            (3600, 9 * 3600, {"app": "google-chrome", "title": "Some article"}),
        ],
        keypress_seconds=list(range(0, 10 * 3600, 30)),
    )
    _enter(ctxs)
    try:
        out = list(repair_afk_events(events))
    finally:
        _exit(ctxs)
    stuck = [e for e in out if e.repair_source == "stuck-window"]
    assert stuck


def test_stuck_window_rescued_by_atuin() -> None:
    """An 8h window stretch with an atuin command in it = operator was
    actually running something. NOT flipped to AFK."""
    events = [_aw("not-afk", 0, 10 * 3600)]
    ctxs = _patch_signals(
        window_events_spec=[
            (3600, 9 * 3600, {"app": "kitty", "title": "long build"}),
        ],
        keypress_seconds=list(range(0, 10 * 3600, 30)),
        atuin_seconds=[3 * 3600],  # operator started a long shell job
    )
    _enter(ctxs)
    try:
        out = list(repair_afk_events(events))
    finally:
        _exit(ctxs)
    # Stuck-window signal should NOT fire — atuin rescues it.
    stuck = [e for e in out if e.repair_source == "stuck-window"]
    assert not stuck


def test_stuck_window_below_threshold_not_flipped() -> None:
    """A 4h focused work session on one window is normal, not AFK."""
    events = [_aw("not-afk", 0, 6 * 3600)]
    ctxs = _patch_signals(
        window_events_spec=[
            (0, 4 * 3600, {"app": "kitty", "title": "deep work"}),
            (4 * 3600, 6 * 3600, {"app": "google-chrome", "title": "reading"}),
        ],
        keypress_seconds=list(range(0, 6 * 3600, 30)),
    )
    _enter(ctxs)
    try:
        out = list(repair_afk_events(events))
    finally:
        _exit(ctxs)
    stuck = [e for e in out if e.repair_source == "stuck-window"]
    assert not stuck


def test_threshold_constant_anchored() -> None:
    assert KEYLOG_SILENT_THRESHOLD_S == 30 * 60
