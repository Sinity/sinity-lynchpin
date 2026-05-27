from __future__ import annotations

from datetime import date

from lynchpin.analysis.operator_public_text import (
    OperatorPublicTextDay,
    monthly_rollup,
    operator_public_text_daily,
)


def test_operator_public_text_empty_range_returns_empty():
    # range with no operator activity (1900s)
    rows = operator_public_text_daily(start=date(1990, 1, 1), end=date(1990, 12, 31))
    assert rows == []


def test_monthly_rollup_collapses_by_month():
    rows = [
        OperatorPublicTextDay(
            date=date(2026, 1, 5), total_chars=100, message_count=2, channel_count=1,
            by_channel={"irc:#x": {"chars": 100, "messages": 2}},
        ),
        OperatorPublicTextDay(
            date=date(2026, 1, 8), total_chars=50, message_count=1, channel_count=1,
            by_channel={"irc:#x": {"chars": 50, "messages": 1}},
        ),
        OperatorPublicTextDay(
            date=date(2026, 2, 1), total_chars=200, message_count=4, channel_count=2,
            by_channel={
                "irc:#x": {"chars": 100, "messages": 2},
                "reddit:python": {"chars": 100, "messages": 2},
            },
        ),
    ]
    rollup = monthly_rollup(rows)
    assert rollup == [
        ("2026-01", 150, 3, 2),
        ("2026-02", 200, 4, 1),
    ]


def test_monthly_rollup_empty_input():
    assert monthly_rollup([]) == []


def test_source_filter_excludes_unselected_collectors(monkeypatch):
    # Validate that when sources={"irc"} only irc collector runs — no errors
    # from un-imported reddit/wykop/messenger sources for a hostile environ.
    rows = operator_public_text_daily(
        start=date(1990, 1, 1), end=date(1990, 12, 31), sources={"irc"}
    )
    assert rows == []
