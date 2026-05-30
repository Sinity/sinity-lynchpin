"""Tests for core/primitives.py: TopN, group_by_gap, interval arithmetic."""

from datetime import date, datetime, timezone
from lynchpin.core.primitives import (
    TopN, group_by_gap,
    merge_intervals, intersect_intervals, split_by_day, split_by_hour,
    duration_s, logical_date,
)
from lynchpin.core.parse import as_local

UTC = timezone.utc


def dt(h, m=0):
    return datetime(2026, 3, 15, h, m, tzinfo=UTC)


class TestTopN:
    def test_basic(self):
        t = TopN(3)
        t.add("a", 10)
        t.add("b", 5)
        t.add("c", 3)
        t.add("d", 1)
        assert t.dominant == "a"
        assert len(t.items) == 3
        assert t.items[0] == ("a", 10)
        assert t.total == 19

    def test_merge(self):
        a = TopN(2)
        a.add("x", 10)
        a.add("y", 5)
        b = TopN(2)
        b.add("x", 3)
        b.add("z", 8)
        merged = a.merge(b)
        assert merged.dominant == "x"
        assert merged.items[0] == ("x", 13)

    def test_empty(self):
        t = TopN()
        assert t.dominant is None
        assert t.items == ()
        assert not t


class TestGroupByGap:
    def test_basic_grouping(self):
        items = [
            (dt(10, 0), dt(10, 10)),
            (dt(10, 11), dt(10, 20)),  # 1min gap, within threshold
            (dt(11, 0), dt(11, 10)),   # 40min gap, new group
        ]
        groups = list(group_by_gap(
            items, start_of=lambda x: x[0], end_of=lambda x: x[1], max_gap=120
        ))
        assert len(groups) == 2
        assert len(groups[0].items) == 2
        assert len(groups[1].items) == 1

    def test_compatible_predicate(self):
        items = [("a", dt(10, 0), dt(10, 10)), ("b", dt(10, 11), dt(10, 20))]
        groups = list(group_by_gap(
            items, start_of=lambda x: x[1], end_of=lambda x: x[2], max_gap=120,
            compatible=lambda a, b: a[0] == b[0],
        ))
        assert len(groups) == 2  # different labels → separate groups

    def test_out_of_order_input_is_sorted(self):
        # Items arriving out of start order must not produce negative gaps that
        # always-merge and move the group end backward. They are sorted on entry.
        items = [
            (dt(11, 0), dt(11, 10)),   # later first
            (dt(10, 0), dt(10, 10)),   # earlier second — 40min gap once sorted
            (dt(10, 11), dt(10, 20)),  # 1min gap to the 10:00 item
        ]
        groups = list(group_by_gap(
            items, start_of=lambda x: x[0], end_of=lambda x: x[1], max_gap=120
        ))
        assert len(groups) == 2
        # Group ordering follows sorted starts; the two 10:xx items group together.
        assert groups[0].start == dt(10, 0)
        assert len(groups[0].items) == 2
        assert groups[1].start == dt(11, 0)

    def test_absorb_interruption(self):
        items = [
            ("a", dt(10, 0), dt(10, 10)),
            ("b", dt(10, 11), datetime(2026, 3, 15, 10, 11, 20, tzinfo=UTC)),  # 20s interruption, absorbed
        ]
        groups = list(group_by_gap(
            items, start_of=lambda x: x[1], end_of=lambda x: x[2], max_gap=120,
            compatible=lambda a, b: a[0] == b[0], absorb_interruption=30,
        ))
        assert len(groups) == 1
        assert groups[0].interruptions == 1
        assert len(groups[0].items) == 2  # "a" + absorbed "b"


class TestIntervals:
    def test_merge(self):
        ivs = [(dt(10), dt(11)), (dt(10, 30), dt(11, 30)), (dt(13), dt(14))]
        merged = merge_intervals(ivs)
        assert len(merged) == 2
        assert merged[0] == (dt(10), dt(11, 30))
        assert merged[1] == (dt(13), dt(14))

    def test_intersect(self):
        timeline = [(dt(10), dt(12)), (dt(14), dt(16))]
        results, idx = intersect_intervals(dt(11), dt(15), timeline, 0)
        assert len(results) == 2
        assert results[0] == (dt(11), dt(12))
        assert results[1] == (dt(14), dt(15))

    def test_merge_mixed_tz_does_not_raise(self):
        # Mix naive (date_to_dt_range style) and tz-aware (as_local style) bounds.
        # The naive bound is the SAME instant as the aware one converted to local,
        # so after normalization the two intervals overlap and merge into one.
        aware = (dt(10), dt(11))               # tz-aware UTC
        naive_same = (as_local(dt(10, 30)).replace(tzinfo=None),
                      as_local(dt(11, 30)).replace(tzinfo=None))  # naive local
        merged = merge_intervals([naive_same, aware])
        # Both bounds normalized to local tz; comparison succeeds (no TypeError).
        assert all(s.tzinfo is not None and e.tzinfo is not None for s, e in merged)
        assert len(merged) == 1

    def test_intersect_mixed_tz_does_not_raise(self):
        naive_span_start = datetime(2026, 3, 15, 11)  # naive
        aware_span_end = dt(15)                        # tz-aware
        timeline = [(dt(10), dt(12)), (dt(14), dt(16))]
        results, _ = intersect_intervals(naive_span_start, aware_span_end, timeline, 0)
        assert len(results) == 2

    def test_split_by_day(self):
        # 22:00→02:00 is within one logical day (boundary at 06:00)
        start = datetime(2026, 3, 15, 22, 0, tzinfo=UTC)
        end = datetime(2026, 3, 16, 2, 0, tzinfo=UTC)
        days = list(split_by_day(start, end))
        assert len(days) == 1
        assert days[0][0] == date(2026, 3, 15)

    def test_split_by_day_crosses_boundary(self):
        # 04:00→08:00 crosses the 06:00 boundary → two logical days
        start = datetime(2026, 3, 15, 4, 0, tzinfo=UTC)
        end = datetime(2026, 3, 15, 8, 0, tzinfo=UTC)
        days = list(split_by_day(start, end))
        assert len(days) == 2
        assert days[0][0] == date(2026, 3, 14)  # 04:00 → Mar 14
        assert days[1][0] == date(2026, 3, 15)  # 06:00+ → Mar 15

    def test_logical_date(self):
        assert logical_date(datetime(2026, 3, 15, 3, 0)) == date(2026, 3, 14)  # 3AM → previous day
        assert logical_date(datetime(2026, 3, 15, 6, 0)) == date(2026, 3, 15)  # 6AM → current day
        assert logical_date(datetime(2026, 3, 15, 23, 0)) == date(2026, 3, 15)  # 11PM → current day

    def test_split_by_hour(self):
        hours = list(split_by_hour(dt(10, 30), dt(12, 15)))
        assert len(hours) == 3

    def test_duration(self):
        assert duration_s((dt(10), dt(11))) == 3600.0
