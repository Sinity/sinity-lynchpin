from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace

from lynchpin.analysis.operator_public_text import (
    OperatorPublicTextDay,
    coverage_summary,
    monthly_rollup,
    operator_public_text_daily,
)
from lynchpin.graph.coverage import CoverageReport
from lynchpin.graph.coverage import SourceCoverage


def test_operator_public_text_empty_range_returns_empty(monkeypatch):
    # range with no operator activity (1900s)
    def noop(_bucket, _start, _end):
        return None

    monkeypatch.setattr("lynchpin.analysis.operator_public_text._collect_irc", noop)
    monkeypatch.setattr("lynchpin.analysis.operator_public_text._collect_reddit", noop)
    monkeypatch.setattr("lynchpin.analysis.operator_public_text._collect_wykop", noop)
    monkeypatch.setattr("lynchpin.analysis.operator_public_text._collect_messenger", noop)
    monkeypatch.setattr("lynchpin.analysis.operator_public_text._collect_gmail", noop)

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
    calls: list[str] = []

    def irc(_bucket, _start, _end):
        calls.append("irc")

    def boom(_bucket, _start, _end):
        raise AssertionError("unselected collector ran")

    monkeypatch.setattr("lynchpin.analysis.operator_public_text._collect_irc", irc)
    monkeypatch.setattr("lynchpin.analysis.operator_public_text._collect_reddit", boom)
    monkeypatch.setattr("lynchpin.analysis.operator_public_text._collect_wykop", boom)
    monkeypatch.setattr("lynchpin.analysis.operator_public_text._collect_messenger", boom)
    monkeypatch.setattr("lynchpin.analysis.operator_public_text._collect_gmail", boom)

    rows = operator_public_text_daily(
        start=date(1990, 1, 1), end=date(1990, 12, 31), sources={"irc"}
    )
    assert rows == []
    assert calls == ["irc"]


def test_irc_collector_uses_bounded_reader(monkeypatch):
    calls: list[tuple[date, date]] = []

    def fake_iter_messages_in_range(*, start: date, end: date, **_kwargs):
        calls.append((start, end))
        yield SimpleNamespace(
            timestamp=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
            is_meta=False,
            speaker="sinity",
            channel="#x",
            text="bounded",
        )

    monkeypatch.setattr("lynchpin.sources.irc_raw.iter_messages_in_range", fake_iter_messages_in_range)

    rows = operator_public_text_daily(
        start=date(2026, 6, 1), end=date(2026, 6, 3), sources={"irc"}
    )

    assert calls == [(date(2026, 6, 1), date(2026, 6, 3))]
    assert rows[0].total_chars == len("bounded")


def test_reddit_collector_uses_half_open_bounded_readers(monkeypatch):
    comment_calls: list[tuple[date | None, date | None]] = []
    post_calls: list[tuple[date | None, date | None]] = []

    def fake_comments(*, start: date | None = None, end: date | None = None, **_kwargs):
        comment_calls.append((start, end))
        yield SimpleNamespace(
            created=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
            body="own text",
            subreddit="python",
        )

    def fake_posts(*, start: date | None = None, end: date | None = None, **_kwargs):
        post_calls.append((start, end))
        yield SimpleNamespace(
            created=datetime(2026, 6, 2, 13, 0, tzinfo=timezone.utc),
            title="Title",
            body="body",
            subreddit="python",
        )

    monkeypatch.setattr("lynchpin.sources.reddit.iter_comments", fake_comments)
    monkeypatch.setattr("lynchpin.sources.reddit.iter_posts", fake_posts)

    rows = operator_public_text_daily(
        start=date(2026, 6, 1), end=date(2026, 6, 3), sources={"reddit"}
    )

    assert comment_calls == [(date(2026, 6, 1), date(2026, 6, 4))]
    assert post_calls == [(date(2026, 6, 1), date(2026, 6, 4))]
    assert rows[0].total_chars == len("own text") + len("Titlebody")


def test_wykop_collector_uses_half_open_bounded_readers(monkeypatch):
    entry_calls: list[tuple[date | None, date | None]] = []
    entry_comment_calls: list[tuple[date | None, date | None]] = []
    link_comment_calls: list[tuple[date | None, date | None]] = []

    def fake_entries(*, start: date | None = None, end: date | None = None, **_kwargs):
        entry_calls.append((start, end))
        yield SimpleNamespace(created_at=datetime(2026, 6, 2, 12), content="entry")

    def fake_entry_comments(*, start: date | None = None, end: date | None = None, **_kwargs):
        entry_comment_calls.append((start, end))
        yield SimpleNamespace(created_at=datetime(2026, 6, 2, 13), content="entry comment")

    def fake_link_comments(*, start: date | None = None, end: date | None = None, **_kwargs):
        link_comment_calls.append((start, end))
        yield SimpleNamespace(created_at=datetime(2026, 6, 2, 14), content="link comment")

    monkeypatch.setattr("lynchpin.sources.exports.iter_wykop_entries", fake_entries)
    monkeypatch.setattr("lynchpin.sources.exports.iter_wykop_entry_comments", fake_entry_comments)
    monkeypatch.setattr("lynchpin.sources.exports.iter_wykop_link_comments", fake_link_comments)

    rows = operator_public_text_daily(
        start=date(2026, 6, 1), end=date(2026, 6, 3), sources={"wykop"}
    )

    assert entry_calls == [(date(2026, 6, 1), date(2026, 6, 4))]
    assert entry_comment_calls == [(date(2026, 6, 1), date(2026, 6, 4))]
    assert link_comment_calls == [(date(2026, 6, 1), date(2026, 6, 4))]
    assert rows[0].total_chars == len("entry") + len("entry comment") + len("link comment")


def test_messenger_collector_uses_half_open_bounded_reader(monkeypatch):
    calls: list[tuple[date | None, date | None]] = []

    def fake_iter_communication_events(*, start: date | None = None, end: date | None = None, **_kwargs):
        calls.append((start, end))
        yield SimpleNamespace(
            direction="outbound",
            timestamp=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
            text_length=7,
            text_excerpt="ignored",
            source="messenger",
        )

    monkeypatch.setattr("lynchpin.sources.communications.iter_communication_events", fake_iter_communication_events)

    rows = operator_public_text_daily(
        start=date(2026, 6, 1), end=date(2026, 6, 3), sources={"messenger"}
    )

    assert calls == [(date(2026, 6, 1), date(2026, 6, 4))]
    assert rows[0].total_chars == 7


def test_gmail_collector_uses_half_open_bounded_reader(monkeypatch):
    calls: list[tuple[date | None, date | None]] = []

    def fake_messages(*, start: date | None = None, end: date | None = None, **_kwargs):
        calls.append((start, end))
        yield SimpleNamespace(
            timestamp=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
            sender="ezo.dev@gmail.com",
            subject="Subject",
            body_preview="Body",
        )

    monkeypatch.setattr("lynchpin.sources.gmail_takeout.iter_materialized_gmail_messages", fake_messages)

    rows = operator_public_text_daily(
        start=date(2026, 6, 1), end=date(2026, 6, 3), sources={"gmail"}
    )

    assert calls == [(date(2026, 6, 1), date(2026, 6, 4))]
    assert rows[0].total_chars == len("SubjectBody")


def test_coverage_summary_marks_untracked_sources(monkeypatch):
    def fake_coverage_report(
        *,
        start: date,
        end: date,
        repair_materializations: bool = True,
    ) -> CoverageReport:
        return CoverageReport(
            start=start,
            end=end,
            generated_at=datetime(2026, 6, 4, tzinfo=timezone.utc),
            sources=(
                SourceCoverage(
                    source="irc",
                    status="available",
                    reason="",
                    requested_start=start,
                    requested_end=end,
                    first_date=start,
                    last_date=end,
                ),
            ),
        )

    monkeypatch.setattr("lynchpin.graph.coverage.coverage_report", fake_coverage_report)

    rows = coverage_summary(start=date(2026, 6, 1), end=date(2026, 6, 4))
    by_source = {row.source: row for row in rows}

    assert by_source["irc"].status == "available"
    assert by_source["wykop"].status == "untracked"
    assert by_source["gmail"].status == "untracked"
    assert by_source["wykop"].reason == "not represented in coverage_report"
