"""Tests for sources/activitywatch.py — focus spans, sessions, deep work, etc.

These are unit tests using synthetic data, not live DB queries.
"""

from datetime import datetime, date, timedelta, timezone
from lynchpin.sources.activitywatch import (
    FocusSpan, AppSession, DeepWorkBlock, CircadianProfile,
    FocusLoop, FragmentationMetrics, AttentionMetrics,
    _merge_adjacent, _focus_stretches, _session_ctx, _deep_compatible,
)
from lynchpin.core.primitives import TopN, group_by_gap

UTC = timezone.utc
def dt(h, m=0, s=0): return datetime(2026, 3, 15, h, m, s, tzinfo=UTC)

def make_span(start, end, kind="focused", app="kitty", title="test", mode="coding", project="sinex"):
    return FocusSpan(start=start, end=end, kind=kind, app=app, title=title, mode=mode, project=project)


class TestFocusSpan:
    def test_duration(self):
        s = make_span(dt(10), dt(11))
        assert s.duration_s == 3600.0

    def test_date(self):
        s = make_span(dt(10), dt(11))
        assert s.date == date(2026, 3, 15)


class TestMergeAdjacent:
    def test_same_shape_merges(self):
        spans = [
            make_span(dt(10, 0), dt(10, 30)),
            make_span(dt(10, 30), dt(11, 0)),
        ]
        merged = list(_merge_adjacent(spans))
        assert len(merged) == 1
        assert merged[0].start == dt(10, 0)
        assert merged[0].end == dt(11, 0)

    def test_different_app_no_merge(self):
        spans = [
            make_span(dt(10), dt(10, 30), app="kitty"),
            make_span(dt(10, 30), dt(11), app="firefox"),
        ]
        merged = list(_merge_adjacent(spans))
        assert len(merged) == 2


class TestSessionCtx:
    def test_project_key(self):
        s = AppSession(app="kitty", start=dt(10), end=dt(11), duration_s=3600,
                       title_dominant="test", titles=("test",), mode="coding", project="sinex", interruptions=0)
        assert _session_ctx(s) == "project:sinex"

    def test_mode_key(self):
        s = AppSession(app="kitty", start=dt(10), end=dt(11), duration_s=3600,
                       title_dominant="test", titles=("test",), mode="coding", project=None, interruptions=0)
        assert _session_ctx(s) == "mode:coding"

    def test_app_key(self):
        s = AppSession(app="kitty", start=dt(10), end=dt(11), duration_s=3600,
                       title_dominant="test", titles=("test",), mode=None, project=None, interruptions=0)
        assert _session_ctx(s) == "app:kitty"


class TestDeepCompatible:
    def test_same_project(self):
        a = AppSession(app="kitty", start=dt(10), end=dt(11), duration_s=3600,
                       title_dominant="t", titles=("t",), mode="coding", project="sinex", interruptions=0)
        b = AppSession(app="kitty", start=dt(11, 5), end=dt(12), duration_s=3300,
                       title_dominant="t", titles=("t",), mode="coding", project="sinex", interruptions=0)
        assert _deep_compatible(a, b)

    def test_different_project(self):
        a = AppSession(app="kitty", start=dt(10), end=dt(11), duration_s=3600,
                       title_dominant="t", titles=("t",), mode="coding", project="sinex", interruptions=0)
        b = AppSession(app="kitty", start=dt(11, 5), end=dt(12), duration_s=3300,
                       title_dominant="t", titles=("t",), mode="coding", project="polylogue", interruptions=0)
        assert not _deep_compatible(a, b)


class TestFocusStretches:
    def test_basic(self):
        sessions = [
            AppSession(app="kitty", start=dt(10), end=dt(10, 30), duration_s=1800,
                       title_dominant="t", titles=("t",), mode="coding", project="sinex", interruptions=0),
            AppSession(app="kitty", start=dt(10, 31), end=dt(11), duration_s=1740,
                       title_dominant="t", titles=("t",), mode="coding", project="sinex", interruptions=0),
            AppSession(app="firefox", start=dt(12), end=dt(12, 30), duration_s=1800,
                       title_dominant="t", titles=("t",), mode="research", project=None, interruptions=0),
        ]
        stretches = _focus_stretches(sessions)
        assert len(stretches) == 2  # first two merge (same ctx, <5min gap), third is separate
