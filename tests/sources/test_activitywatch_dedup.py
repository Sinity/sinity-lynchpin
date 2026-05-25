"""Tests for AW dedup+merge.

Pin behavior against the two upstream bugs we observed in the operator's
live DB on 2026-05-25:

  1. Window/chrome events: ~95k zero-duration heartbeats per day arriving
     ~2 s apart (poll-vs-pulsetime mismatch). Should collapse to one
     interval per (data, contiguous-gap) run.

  2. AFK clusters: 91+ events sharing one starttime with monotonically
     growing endtime (PR #555 fix incomplete — merged_heartbeat returned
     with id=None). Should collapse to a single row at max endtime.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lynchpin.sources.activitywatch_dedup import (
    MERGE_PULSETIME_S,
    dedup_and_merge,
)
from lynchpin.sources.activitywatch_raw import AWEvent


UTC = timezone.utc


def E(bucket: str, ts_s: float, dur_s: float, app: str = "kitty",
      title: str = "x") -> AWEvent:
    start = datetime(2026, 5, 25, tzinfo=UTC) + timedelta(seconds=ts_s)
    end = start + timedelta(seconds=dur_s)
    return AWEvent(
        bucket=bucket,
        start=start,
        end=end,
        data={"app": app, "title": title},
    )


def test_window_zero_duration_run_merges_into_one_interval():
    """5 consecutive same-data zero-duration heartbeats 2s apart → 1 interval."""
    bucket = "aw-watcher-window_sinnix-prime"
    inputs = [
        E(bucket, 0, 0, title="YouTube"),
        E(bucket, 2, 0, title="YouTube"),
        E(bucket, 4, 0, title="YouTube"),
        E(bucket, 6, 0, title="YouTube"),
        E(bucket, 8, 0, title="YouTube"),
    ]
    out = list(dedup_and_merge(inputs))
    assert len(out) == 1
    assert out[0].data == {"app": "kitty", "title": "YouTube"}
    # End should cover the full 8-second span
    span = (out[0].end - out[0].start).total_seconds()
    assert span >= 8


def test_window_different_data_does_not_merge():
    """Braille-spinner case: consecutive heartbeats with different titles → distinct events."""
    bucket = "aw-watcher-window_sinnix-prime"
    inputs = [
        E(bucket, 0, 0, title="\u2807 sinex"),
        E(bucket, 2, 0, title="\u283c sinex"),
        E(bucket, 4, 0, title="\u2838 sinex"),
    ]
    out = list(dedup_and_merge(inputs))
    # Three distinct titles → three rows
    assert len(out) == 3


def test_window_run_with_large_gap_breaks_merge():
    """Two same-data runs separated by a 60s gap (> MERGE_PULSETIME_S=30) → 2 runs."""
    assert MERGE_PULSETIME_S == 30.0  # if this changes, update the test
    bucket = "aw-watcher-window_sinnix-prime"
    inputs = [
        E(bucket, 0, 0, title="YouTube"),
        E(bucket, 2, 0, title="YouTube"),
        E(bucket, 4, 0, title="YouTube"),
        # 60s gap (clearly broke the activity stretch)
        E(bucket, 64, 0, title="YouTube"),
        E(bucket, 66, 0, title="YouTube"),
    ]
    out = list(dedup_and_merge(inputs))
    assert len(out) == 2


def test_afk_duplicate_starttime_cluster_collapses():
    """91 events sharing one starttime → one row with max endtime."""
    bucket = "aw-watcher-afk_sinnix-prime"
    inputs = []
    for endtime_s in range(1080, 1080 + 91 * 10, 10):
        # All share start=0; endtimes grow from 1080s to 1980s
        e = E(bucket, 0, endtime_s, app="afk", title="afk")
        # Override data to look like real AFK row
        e = AWEvent(
            bucket=bucket, start=e.start, end=e.end,
            data={"status": "afk"},
        )
        inputs.append(e)
    out = list(dedup_and_merge(inputs))
    assert len(out) == 1
    # Max endtime is the LAST event's endtime
    assert (out[0].end - out[0].start).total_seconds() >= 1980 - 10


def test_pass_through_when_data_already_clean():
    """Healthy AW data (single-bucket, well-merged intervals) survives unchanged."""
    bucket = "aw-watcher-window_sinnix-prime"
    inputs = [
        E(bucket, 0, 30, title="A"),
        E(bucket, 30, 45, title="B"),
        E(bucket, 75, 90, title="C"),
    ]
    out = list(dedup_and_merge(inputs))
    assert len(out) == 3
    assert [e.data["title"] for e in out] == ["A", "B", "C"]


def test_cross_bucket_isolation():
    """Events from different buckets don't merge with each other."""
    win = "aw-watcher-window_sinnix-prime"
    afk = "aw-watcher-afk_sinnix-prime"
    inputs = [
        E(win, 0, 0, title="A"),
        E(win, 2, 0, title="A"),
        E(afk, 4, 5, title="afk"),
        E(win, 10, 0, title="A"),  # different bucket between rows
    ]
    out = list(dedup_and_merge(inputs))
    # Order in output is per-bucket; check counts and ungrouped state
    by_bucket = {b: [e for e in out if e.bucket == b] for b in (win, afk)}
    # 3 window events with title=A — first two merge, third stays separate
    # because they're separated by a non-window event in input.
    # Implementation flushes window buffer on bucket change so we get:
    #   first window run: [0..2] = 1 merged event
    #   afk: [4..9] = 1 event
    #   second window run: [10..10] = 1 event (after re-buffering)
    assert len(by_bucket[win]) == 2
    assert len(by_bucket[afk]) == 1


def test_overlapping_events_keep_max_end():
    """If two same-(start, data) rows overlap, keep the one with later end."""
    bucket = "aw-watcher-afk_sinnix-prime"
    s = datetime(2026, 5, 25, tzinfo=UTC)
    inputs = [
        AWEvent(bucket=bucket, start=s, end=s + timedelta(seconds=100),
                data={"status": "afk"}),
        AWEvent(bucket=bucket, start=s, end=s + timedelta(seconds=300),
                data={"status": "afk"}),
        AWEvent(bucket=bucket, start=s, end=s + timedelta(seconds=200),
                data={"status": "afk"}),
    ]
    out = list(dedup_and_merge(inputs))
    assert len(out) == 1
    assert (out[0].end - out[0].start).total_seconds() == 300
