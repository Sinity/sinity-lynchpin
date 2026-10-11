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
        patch(
            "lynchpin.sources.activitywatch_outages.afk_events", return_value=iter(afk)
        ),
        patch(
            "lynchpin.sources.activitywatch_outages.window_events",
            return_value=iter(win),
        ),
        patch(
            "lynchpin.sources.activitywatch_outages.web_events", return_value=iter(web)
        ),
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
    win = [
        SimpleNamespace(
            start=BASE + timedelta(seconds=s),
            end=BASE + timedelta(seconds=s + 5),
            data={"app": "x"},
        )
        for s in range(3000, 35000, 300)
    ]
    web = [
        SimpleNamespace(
            start=BASE + timedelta(seconds=s),
            end=BASE + timedelta(seconds=s + 5),
            data={},
        )
        for s in range(3000, 35000, 600)
    ]
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
    web = [
        SimpleNamespace(
            start=BASE + timedelta(seconds=s),
            end=BASE + timedelta(seconds=s + 5),
            data={},
        )
        for s in range(3000, 35000, 300)
    ]
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


def test_daily_outage_normalizes_naive_logical_day_bounds(monkeypatch):
    from lynchpin.sources import activitywatch as aw

    monkeypatch.setattr("lynchpin.core.parse.local_tz", lambda: UTC)
    with patch(
        "lynchpin.sources.activitywatch_outages.afk_events", return_value=[_ev(0, 3600)]
    ), patch(
        "lynchpin.sources.activitywatch_outages.window_events", return_value=[]
    ), patch("lynchpin.sources.activitywatch_outages.web_events", return_value=[]):
        result = aw._daily_outage_hours(
            start=BASE.replace(tzinfo=None),
            end=(BASE + timedelta(hours=2)).replace(tzinfo=None),
            ensure=False,
        )
    assert sum(result.values()) == 3600


def test_outage_gap_uses_instants_in_repeated_hour():
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Europe/Warsaw")
    start = datetime(2025, 10, 26, 2, 10, tzinfo=zone, fold=0)
    end = datetime(2025, 10, 26, 2, 20, tzinfo=zone, fold=1)
    with patch(
        "lynchpin.sources.activitywatch_outages.afk_events", return_value=[]
    ), patch(
        "lynchpin.sources.activitywatch_outages.window_events", return_value=[]
    ), patch("lynchpin.sources.activitywatch_outages.web_events", return_value=[]):
        rows = detect_data_outages(start=start, end=end, ensure=False)
    assert rows[0].duration_s == 4200


def test_daily_outage_keeps_unavailable_source_explicit():
    import pytest
    from lynchpin.sources import activitywatch as aw
    from lynchpin.core.errors import SourceUnavailableError

    with patch(
        "lynchpin.sources.activitywatch_outages.afk_events",
        side_effect=SourceUnavailableError(
            "activitywatch", reason="unavailable selected partition"
        ),
    ):
        with pytest.raises(
            SourceUnavailableError, match="unavailable selected partition"
        ):
            aw._daily_outage_hours(
                start=BASE, end=BASE + timedelta(hours=2), ensure=False
            )


def test_daily_outage_splits_long_gap_at_logical_boundaries(monkeypatch):
    from datetime import date
    from lynchpin.sources import activitywatch as aw

    monkeypatch.setattr("lynchpin.core.parse.local_tz", lambda: UTC)
    start = BASE + timedelta(hours=5)
    end = BASE + timedelta(days=1, hours=7)
    with patch(
        "lynchpin.sources.activitywatch_outages.afk_events", return_value=[]
    ), patch(
        "lynchpin.sources.activitywatch_outages.window_events", return_value=[]
    ), patch("lynchpin.sources.activitywatch_outages.web_events", return_value=[]):
        values = aw._daily_outage_hours(start=start, end=end, ensure=False)
    assert values == {
        date(2026, 4, 30): 3600,
        date(2026, 5, 1): 86400,
        date(2026, 5, 2): 3600,
    }


def test_continuing_window_event_is_coverage_during_afk_gap():
    with patch(
        "lynchpin.sources.activitywatch_outages.afk_events",
        return_value=[_ev(0, 3600), _ev(10800, 14400)],
    ), patch(
        "lynchpin.sources.activitywatch_outages.window_events",
        return_value=[_ev(0, 14400)],
    ), patch("lynchpin.sources.activitywatch_outages.web_events", return_value=[]):
        rows = detect_data_outages(
            start=BASE, end=BASE + timedelta(hours=4), ensure=False
        )
    assert len(rows) == 1
    assert rows[0].pattern == "C"
    assert rows[0].window_events == 1


def test_afk_gap_orders_repeated_hour_events_by_instant():
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Europe/Warsaw")
    start = datetime(2025, 10, 26, 2, 0, tzinfo=zone, fold=0)
    first_end = datetime(2025, 10, 26, 2, 10, tzinfo=zone, fold=0)
    next_start = datetime(2025, 10, 26, 2, 20, tzinfo=zone, fold=1)
    end = datetime(2025, 10, 26, 2, 30, tzinfo=zone, fold=1)
    afk = [
        AWEvent("aw-watcher-afk_fixture", next_start, end, {}),
        AWEvent("aw-watcher-afk_fixture", start, first_end, {}),
    ]
    with patch(
        "lynchpin.sources.activitywatch_outages.afk_events", return_value=afk
    ), patch(
        "lynchpin.sources.activitywatch_outages.window_events", return_value=[]
    ), patch("lynchpin.sources.activitywatch_outages.web_events", return_value=[]):
        rows = detect_data_outages(start=start, end=end, ensure=False)
    assert len(rows) == 1
    assert rows[0].duration_s == 4200
