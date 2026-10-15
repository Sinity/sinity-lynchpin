from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("partition_start", [None, date(2026, 5, 2)])
def test_promote_personal_sources_bounds_activity_content_reads(monkeypatch, tmp_path, partition_start) -> None:
    pinned = (tmp_path / "generation" / "daily.ndjson", tmp_path / "generation" / "title_usage.ndjson")
    next_selection = (tmp_path / "later" / "daily.ndjson", tmp_path / "later" / "title_usage.ndjson")
    selection = [pinned]
    monkeypatch.setattr("lynchpin.sources.activity_content.activity_content_bundle_paths", lambda: selection[0])
    from lynchpin.analysis.active.substrate_promote_personal import promote_personal_sources
    from lynchpin.substrate.source_status import (
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
        assert window == (partition_start or date(2026, 5, 1), date(2026, 5, 4))
        return SimpleNamespace(status="ready", reason="ready")

    def fake_iter_activity_content_days(*, path=None, start=None, end=None, ensure=True):
        assert path == pinned[0]
        selection[0] = next_selection
        content_windows.append((start, end, ensure))
        yield ContentRow(date=start)

    def fake_iter_activity_title_usage(*, path=None, start=None, end=None, ensure=True):
        assert path == pinned[1]
        usage_windows.append((start, end, ensure))
        yield UsageRow(first_date=date(2026, 1, 1), last_date=date(2026, 1, 1))

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
        lambda _conn, *, rows, **_kwargs: len(list(rows)),
    )
    monkeypatch.setattr(
        "lynchpin.substrate.personal.promote_activity_content_buckets",
        lambda _conn, *, rows, **_kwargs: len(list(rows)),
    )
    monkeypatch.setattr(
        "lynchpin.substrate.personal.promote_activity_title_usage",
        lambda _conn, *, rows, **_kwargs: len(list(rows)),
    )
    monkeypatch.setattr(
        "lynchpin.analysis.active.substrate_promote_personal.record_source_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.analysis.active.substrate_promote_personal._latest_product_refresh",
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
        partition_window_start=partition_start,
    )

    assert content_windows == [(partition_start or date(2026, 5, 1), date(2026, 5, 4), False)]
    assert usage_windows == [(None, None, False)]
    assert counts["activity_content_day"] == 1
    assert counts["activity_content_bucket"] == 1
    assert counts["activity_title_usage"] == 1


def test_promote_personal_sources_uses_preconverged_personal_products(monkeypatch) -> None:
    monkeypatch.setattr(
        "lynchpin.sources.personal_signals.spotify_daily_window",
        lambda *, start, end: (start, end),
    )
    from lynchpin.analysis.active.substrate_promote_personal import promote_personal_sources
    from lynchpin.substrate.source_status import (
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
        lambda _conn, *, rows, **_kwargs: len(list(rows)),
    )
    monkeypatch.setattr(
        "lynchpin.substrate.personal.promote_personal_daily_signals",
        lambda _conn, *, rows, **_kwargs: len(list(rows)),
    )
    monkeypatch.setattr(
        "lynchpin.analysis.active.substrate_promote_personal.record_source_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.analysis.active.substrate_promote_personal._latest_product_refresh",
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
    from lynchpin.substrate.source_status import SourceSelection

    calls: list[tuple[date, date]] = []
    promoted_dates: list[date] = []

    def fake_operator_daily_matrix(start: date, end: date, *, connection):
        assert connection is not None
        calls.append((start, end))
        return [
            SimpleNamespace(date=date(2026, 5, 1)),
            SimpleNamespace(date=date(2026, 5, 2)),
        ]

    def fake_promote_operator_day_rows(_conn, *, refresh_id, rows, previous_refresh_id, replacement_start, replacement_end):
        assert (previous_refresh_id, replacement_start, replacement_end) == (None, None, None)
        del refresh_id
        materialized = list(rows)
        promoted_dates.extend(row.date for row in materialized)
        return len(materialized)

    monkeypatch.setattr("lynchpin.analysis.operator_daily.operator_daily_matrix", fake_operator_daily_matrix)
    monkeypatch.setattr("lynchpin.substrate.personal.promote_operator_day_rows", fake_promote_operator_day_rows)
    monkeypatch.setattr(
        "lynchpin.substrate.readers_signals.load_operator_day_window",
        lambda _conn, *, refresh_id: (date(2026, 5, 1), date(2026, 5, 1), 1),
    )
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


@pytest.mark.parametrize("overlap", [True, False])
def test_spotify_promotion_keeps_export_tail_unavailable(monkeypatch, overlap) -> None:
    from lynchpin.analysis.active.substrate_promote_personal import promote_personal_sources
    from lynchpin.substrate.source_status import SOURCE_SPOTIFY_DAILY, SourceSelection

    requested = (date(2030, 1, 1), date(2030, 1, 8))
    clipped = (date(2030, 1, 1), date(2030, 1, 4))
    monkeypatch.setattr("lynchpin.sources.personal_signals.spotify_daily_window", lambda **kw: clipped if overlap else None)
    ensured, reads, statuses = [], [], []
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", lambda name, *, window: (
        ensured.append(window) or SimpleNamespace(status="ready", reason="fixture")
    ))
    monkeypatch.setattr("lynchpin.sources.personal_signals.iter_spotify_daily_signals", lambda **kw: (
        reads.append(kw) or [SimpleNamespace(date=clipped[0])]
    ))
    monkeypatch.setattr("lynchpin.substrate.personal.promote_spotify_daily_rows", lambda conn, **kw: len(kw["rows"]))
    monkeypatch.setattr("lynchpin.analysis.active.substrate_promote_personal.record_source_status", lambda conn, **kw: statuses.append(kw))
    counts = {}
    promote_personal_sources(
        SimpleNamespace(), refresh_id="fixture", window_start=requested[0], window_end=requested[1],
        counts=counts, selection=SourceSelection.from_collection({SOURCE_SPOTIFY_DAILY}),
    )
    status = statuses[0]
    assert (status["window_start"], status["window_end"]) == requested
    if overlap:
        assert ensured == [clipped]
        assert reads == [{"start": clipped[0], "end": clipped[1], "ensure": False}]
        assert status["status"] == "partial"
        assert status["row_count"] == 1
        assert "outside dates are unavailable" in status["reason"]
    else:
        assert ensured == reads == []
        assert status["status"] == "unavailable"
        assert status["row_count"] is None
        assert "spotify_daily" not in counts


@pytest.mark.parametrize("failure", ["absent-export", "daily-hole", "invalid-carrier"])
def test_spotify_optional_absence_does_not_hide_broken_daily_product(monkeypatch, failure) -> None:
    from lynchpin.analysis.active.substrate_promote_personal import promote_personal_sources
    from lynchpin.core.errors import SourceUnavailableError
    from lynchpin.substrate.source_status import SOURCE_SPOTIFY_DAILY, SourceSelection

    def window(**kwargs):
        if failure == "absent-export":
            raise SourceUnavailableError("spotify", reason="export absent")
        return date(2030, 1, 1), date(2030, 1, 4)

    def ensure(name, *, window):
        return SimpleNamespace(status="failed" if failure == "daily-hole" else "ready", reason="fixture")

    def read(**kwargs):
        raise SourceUnavailableError("personal_daily_signals", reason="invalid carrier")

    monkeypatch.setattr("lynchpin.sources.personal_signals.spotify_daily_window", window)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", ensure)
    monkeypatch.setattr("lynchpin.sources.personal_signals.iter_spotify_daily_signals", read)
    statuses = []
    monkeypatch.setattr("lynchpin.analysis.active.substrate_promote_personal.record_source_status", lambda conn, **kw: statuses.append(kw))
    promote_personal_sources(
        SimpleNamespace(), refresh_id="fixture", window_start=date(2030, 1, 1), window_end=date(2030, 1, 8),
        counts={}, selection=SourceSelection.from_collection({SOURCE_SPOTIFY_DAILY}),
    )
    assert statuses[0]["status"] == ("unavailable" if failure == "absent-export" else "error")
    assert statuses[0]["row_count"] is None


@pytest.mark.parametrize("empty_tail", [False, True])
def test_operator_day_incremental_promoter_reads_tail_and_retains_predecessor(monkeypatch, tmp_path, empty_tail):
    from lynchpin.analysis.active.substrate_promote_personal import promote_personal_sources
    from lynchpin.analysis.operator_daily import OperatorDay
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import promote_operator_day_rows, load_operator_day_rows
    from lynchpin.substrate.readers_signals import load_operator_day_window, load_operator_day_lag_correlation

    calls = []
    start, end = date(2026, 10, 8), date(2026, 10, 16)
    def matrix(lower, upper, *, connection):
        calls.append((lower, upper))
        assert (lower, upper) == (start, date(2026, 10, 15))
        return [] if empty_tail else [OperatorDay(date=date(2026, 10, 12), git_commits=3, web_visits=6, spotify_hours=None, sources_present=frozenset({"git"}))]
    monkeypatch.setattr("lynchpin.analysis.operator_daily.operator_daily_matrix", matrix)
    only_operator = SimpleNamespace(sources=None, includes=lambda *sources: len(sources) > 1)
    with connect(tmp_path / "operator.duckdb") as conn:
        apply_schema(conn)
        promote_operator_day_rows(conn, refresh_id="base", rows=[
            OperatorDay(date=date(2026, 10, 1), git_commits=1, web_visits=2),
            OperatorDay(date=date(2026, 10, 10), git_commits=99, web_visits=99, spotify_hours=0),
            OperatorDay(date=date(2026, 10, 20), git_commits=5, web_visits=10),
        ])
        counts = {}
        promote_personal_sources(conn, refresh_id="tail", window_start=date(2011, 1, 1), window_end=end,
                                 partition_window_start=start, counts=counts, selection=only_operator)
        assert calls == [(start, date(2026, 10, 15))]
        assert counts["operator_day"] == (0 if empty_tail else 1)
        from lynchpin.substrate.snapshots import best_materialized_refresh_id
        assert best_materialized_refresh_id(conn, "operator_day", caller="test") == "tail"
        assert best_materialized_refresh_id(conn, "operator_day", caller="test", start=start, end=end) == "tail"
        rows = load_operator_day_rows(conn, refresh_id="tail")
        expected = [date(2026, 10, 1), date(2026, 10, 20)] if empty_tail else [date(2026, 10, 1), date(2026, 10, 12), date(2026, 10, 20)]
        assert [r["date"] for r in rows] == expected
        assert conn.execute("SELECT status,row_count FROM substrate_source_status WHERE refresh_id='tail' AND source='operator_day'").fetchone() == ("ok", len(expected))
        assert load_operator_day_window(conn, refresh_id="tail") == (expected[0], expected[-1], len(expected))
        correlation, n = load_operator_day_lag_correlation(conn, refresh_id="tail", metric_a="git_commits", metric_b="web_visits", lag=0)
        assert n == len(expected)
        assert correlation == pytest.approx(1)
        if not empty_tail:
            assert rows[1]["spotify_hours"] is None
            assert rows[1]["sources_present"] == ["git"]
        assert conn.execute("SELECT COUNT(*) FROM operator_day WHERE refresh_id='tail'").fetchone()[0] == (0 if empty_tail else 1)


def test_operator_day_incremental_without_verified_base_never_bootstraps_history(monkeypatch, tmp_path, caplog):
    from lynchpin.analysis.active.substrate_promote_personal import promote_personal_sources
    from lynchpin.substrate.connection import apply_schema, connect
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("incremental request must not bootstrap global history")
    monkeypatch.setattr("lynchpin.analysis.operator_daily.operator_daily_matrix", forbidden)
    only_operator = SimpleNamespace(sources=None, includes=lambda *sources: len(sources) > 1)
    with connect(tmp_path / "empty.duckdb") as conn:
        apply_schema(conn)
        counts = {}
        promote_personal_sources(conn, refresh_id="tail", window_start=date(2011, 1, 1), window_end=date(2026, 10, 16),
                                 partition_window_start=date(2026, 10, 8), counts=counts, selection=only_operator)
        assert calls == []
        assert "operator_day" not in counts
        assert "predecessor" in caplog.text


@pytest.mark.parametrize("partition_start", [date(2026, 10, 16), date(2010, 1, 1)])
def test_operator_day_invalid_replacement_refuses_before_matrix(monkeypatch, tmp_path, partition_start):
    from lynchpin.analysis.active.substrate_promote_personal import promote_personal_sources
    from lynchpin.substrate.connection import apply_schema, connect
    calls = []
    monkeypatch.setattr("lynchpin.analysis.operator_daily.operator_daily_matrix", lambda *args, **kwargs: calls.append(args))
    with connect(tmp_path / "invalid.duckdb") as conn:
        apply_schema(conn)
        promote_personal_sources(conn, refresh_id="invalid", window_start=date(2011, 1, 1), window_end=date(2026, 10, 16),
                                 partition_window_start=partition_start, counts={}, selection=SimpleNamespace(sources=None, includes=lambda *sources: len(sources) > 1))
        assert calls == []
        assert conn.execute("SELECT status FROM substrate_source_status WHERE source='operator_day'").fetchone() == ("error",)
