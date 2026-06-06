from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace


def test_promote_personal_sources_bounds_activity_content_reads(monkeypatch) -> None:
    from lynchpin.analysis.active.substrate_promote_personal import promote_personal_sources
    from lynchpin.analysis.active.substrate_promote_status import (
        SOURCE_ACTIVITY_CONTENT,
        SourceSelection,
    )

    content_windows: list[tuple[date | None, date | None, bool]] = []
    usage_windows: list[tuple[date | None, date | None, bool]] = []

    @dataclass
    class ContentRow:
        date: date

    @dataclass
    class UsageRow:
        first_date: date | None
        last_date: date | None

    def fake_ensure_materialized(name: str, *, window=None):
        assert name == "activity_content"
        assert window == (date(2026, 5, 1), date(2026, 5, 4))
        return SimpleNamespace(status="ready", reason="ready")

    def fake_iter_activity_content_days(*, start=None, end=None, ensure=True):
        content_windows.append((start, end, ensure))
        yield ContentRow(date=start)

    def fake_iter_activity_title_usage(*, start=None, end=None, ensure=True):
        usage_windows.append((start, end, ensure))
        yield UsageRow(first_date=start, last_date=start)

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.sources.activity_content.iter_activity_content_days",
        fake_iter_activity_content_days,
    )
    monkeypatch.setattr(
        "lynchpin.sources.activity_content.iter_activity_title_usage",
        fake_iter_activity_title_usage,
    )
    monkeypatch.setattr(
        "lynchpin.substrate.personal.promote_activity_content_days",
        lambda _conn, *, refresh_id, rows: len(list(rows)),
    )
    monkeypatch.setattr(
        "lynchpin.substrate.personal.promote_activity_content_buckets",
        lambda _conn, *, refresh_id, rows: len(list(rows)),
    )
    monkeypatch.setattr(
        "lynchpin.substrate.personal.promote_activity_title_usage",
        lambda _conn, *, refresh_id, rows: len(list(rows)),
    )
    monkeypatch.setattr(
        "lynchpin.analysis.active.substrate_promote_personal.record_source_status",
        lambda *_args, **_kwargs: None,
    )

    counts: dict[str, int] = {}
    promote_personal_sources(
        SimpleNamespace(),
        refresh_id="rid",
        window_start=date(2026, 5, 1),
        window_end=date(2026, 5, 4),
        counts=counts,
        selection=SourceSelection.from_collection({SOURCE_ACTIVITY_CONTENT}),
    )

    assert content_windows == [(date(2026, 5, 1), date(2026, 5, 4), False)]
    assert usage_windows == [(date(2026, 5, 1), date(2026, 5, 4), False)]
    assert counts["activity_content_day"] == 1
    assert counts["activity_content_bucket"] == 1
    assert counts["activity_title_usage"] == 1


def test_promote_personal_sources_uses_preconverged_personal_products(monkeypatch) -> None:
    from lynchpin.analysis.active.substrate_promote_personal import promote_personal_sources
    from lynchpin.analysis.active.substrate_promote_status import (
        SOURCE_PERSONAL_DAILY_SIGNAL,
        SOURCE_SPOTIFY_DAILY,
        SourceSelection,
    )

    ensure_calls: list[tuple[str, tuple[date, date] | None]] = []
    read_calls: list[tuple[str, date | None, date | None, bool]] = []

    def fake_ensure_materialized(name: str, *, window=None):
        ensure_calls.append((name, window))
        return SimpleNamespace(status="ready", reason="ready")

    def fake_iter_spotify_daily_signals(*, start=None, end=None, ensure=True):
        read_calls.append(("spotify_daily", start, end, ensure))
        yield SimpleNamespace(date=start)

    def fake_iter_personal_daily_signals(*, start=None, end=None, ensure=True):
        read_calls.append(("personal_daily_signals", start, end, ensure))
        yield SimpleNamespace(
            source="keylog",
            date=start,
            metric="keypress_count",
            value=1.0,
            dimensions={},
        )

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.sources.personal_signals.iter_spotify_daily_signals",
        fake_iter_spotify_daily_signals,
    )
    monkeypatch.setattr(
        "lynchpin.sources.personal_signals.iter_personal_daily_signals",
        fake_iter_personal_daily_signals,
    )
    monkeypatch.setattr(
        "lynchpin.substrate.personal.promote_spotify_daily_rows",
        lambda _conn, *, refresh_id, rows: len(list(rows)),
    )
    monkeypatch.setattr(
        "lynchpin.substrate.personal.promote_personal_daily_signals",
        lambda _conn, *, refresh_id, rows: len(list(rows)),
    )
    monkeypatch.setattr(
        "lynchpin.analysis.active.substrate_promote_personal.record_source_status",
        lambda *_args, **_kwargs: None,
    )

    counts: dict[str, int] = {}
    promote_personal_sources(
        SimpleNamespace(),
        refresh_id="rid",
        window_start=date(2026, 5, 1),
        window_end=date(2026, 5, 4),
        counts=counts,
        selection=SourceSelection.from_collection({
            SOURCE_SPOTIFY_DAILY,
            SOURCE_PERSONAL_DAILY_SIGNAL,
        }),
    )

    assert ensure_calls == [
        ("spotify_daily", (date(2026, 5, 1), date(2026, 5, 4))),
        ("personal_daily_signals", (date(2026, 5, 1), date(2026, 5, 4))),
    ]
    assert read_calls == [
        ("spotify_daily", date(2026, 5, 1), date(2026, 5, 4), False),
        ("personal_daily_signals", date(2026, 5, 1), date(2026, 5, 4), False),
    ]
    assert counts["spotify_daily"] == 1
    assert counts["personal_daily_signal"] == 1


def test_promote_personal_sources_translates_operator_day_window(monkeypatch) -> None:
    from lynchpin.analysis.active.substrate_promote_personal import promote_personal_sources
    from lynchpin.analysis.active.substrate_promote_status import SourceSelection

    calls: list[tuple[date, date]] = []
    promoted_dates: list[date] = []

    def fake_operator_daily_matrix(start: date, end: date):
        calls.append((start, end))
        return [
            SimpleNamespace(date=date(2026, 5, 1)),
            SimpleNamespace(date=date(2026, 5, 2)),
        ]

    def fake_promote_operator_day_rows(_conn, *, refresh_id, rows):
        del refresh_id
        materialized = list(rows)
        promoted_dates.extend(row.date for row in materialized)
        return len(materialized)

    monkeypatch.setattr("lynchpin.analysis.operator_daily.operator_daily_matrix", fake_operator_daily_matrix)
    monkeypatch.setattr("lynchpin.substrate.personal.promote_operator_day_rows", fake_promote_operator_day_rows)
    monkeypatch.setattr(
        "lynchpin.analysis.active.substrate_promote_personal.record_source_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda *_args, **_kwargs: SimpleNamespace(status="blocked", reason="not under test"),
    )

    counts: dict[str, int] = {}
    promote_personal_sources(
        SimpleNamespace(),
        refresh_id="rid",
        window_start=date(2026, 5, 1),
        window_end=date(2026, 5, 2),
        counts=counts,
        selection=SourceSelection.from_collection(None),
    )

    assert calls == [(date(2026, 5, 1), date(2026, 5, 1))]
    assert promoted_dates == [date(2026, 5, 1)]
    assert counts["operator_day"] == 1
