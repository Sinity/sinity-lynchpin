"""Tests for AW data outage detection."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from types import SimpleNamespace

from lynchpin.sources.activitywatch_models import AWEvent
from lynchpin.sources.activitywatch_outages import (
    OUTAGE_THRESHOLD_S,
    detect_data_outages,
)

UTC = timezone.utc
BASE = datetime(2026, 5, 1, tzinfo=UTC)


def _ev(start_s: int, end_s: int, status: str = "not-afk") -> AWEvent:
    return AWEvent(
        bucket="aw-watcher-afk_test",
        start=BASE + timedelta(seconds=start_s),
        end=BASE + timedelta(seconds=end_s),
        data={"status": status},
    )


def _patch(afk, win, web):
    return [
        patch("lynchpin.sources.activitywatch_outages.afk_events", return_value=iter(afk)),
        patch("lynchpin.sources.activitywatch_outages.window_events", return_value=iter(win)),
        patch("lynchpin.sources.activitywatch_outages.web_events", return_value=iter(web)),
    ]


def test_no_outage_when_afk_continuous():
    """Heartbeats every 5 min in afk bucket → no outage."""
    afk = [_ev(s, s + 300) for s in range(0, 24 * 3600, 300)]
    ctxs = _patch(afk, [], [])
    [c.__enter__() for c in ctxs]
    try:
        out = detect_data_outages(start=BASE, end=BASE + timedelta(days=1))
    finally:
        for c in reversed(ctxs):
            c.__exit__(None, None, None)
    assert out == []


def test_pattern_a_all_buckets_silent():
    """All 3 silent → pattern A (real outage)."""
    afk = [_ev(0, 300), _ev(36000, 36300)]  # gap 5min-10h
    ctxs = _patch(afk, [], [])
    [c.__enter__() for c in ctxs]
    try:
        out = detect_data_outages(start=BASE, end=BASE + timedelta(seconds=36300))
    finally:
        for c in reversed(ctxs):
            c.__exit__(None, None, None)
    assert len(out) == 1
    assert out[0].pattern == "A"


def test_pattern_c_afk_only_down():
    """afk silent but window+web running → pattern C."""
    afk = [_ev(0, 300), _ev(36000, 36300)]
    win = [SimpleNamespace(start=BASE + timedelta(seconds=s),
                            end=BASE + timedelta(seconds=s + 5),
                            data={"app": "x"}) for s in range(3000, 35000, 300)]
    web = [SimpleNamespace(start=BASE + timedelta(seconds=s),
                            end=BASE + timedelta(seconds=s + 5),
                            data={}) for s in range(3000, 35000, 600)]
    ctxs = _patch(afk, win, web)
    [c.__enter__() for c in ctxs]
    try:
        out = detect_data_outages(start=BASE, end=BASE + timedelta(seconds=36300))
    finally:
        for c in reversed(ctxs):
            c.__exit__(None, None, None)
    assert len(out) == 1
    assert out[0].pattern == "C"
    assert out[0].window_events > 0
    assert out[0].web_events > 0


def test_pattern_b_afk_and_window_down():
    """afk+window silent, web running → pattern B (awatcher died)."""
    afk = [_ev(0, 300), _ev(36000, 36300)]
    win = []
    web = [SimpleNamespace(start=BASE + timedelta(seconds=s),
                            end=BASE + timedelta(seconds=s + 5),
                            data={}) for s in range(3000, 35000, 300)]
    ctxs = _patch(afk, win, web)
    [c.__enter__() for c in ctxs]
    try:
        out = detect_data_outages(start=BASE, end=BASE + timedelta(seconds=36300))
    finally:
        for c in reversed(ctxs):
            c.__exit__(None, None, None)
    assert len(out) == 1
    assert out[0].pattern == "B"


def test_threshold_anchored():
    assert OUTAGE_THRESHOLD_S == 30 * 60
