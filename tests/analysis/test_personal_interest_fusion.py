from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from lynchpin.analysis.personal_interest_fusion import personal_interest_trace
from lynchpin.sources.bookmarks import BookmarkEvent
from lynchpin.sources.google_takeout_products import GoogleTakeoutEvent


UTC = timezone.utc


def test_personal_interest_trace_fuses_search_bookmark_and_domain_sources() -> None:
    google = [
        GoogleTakeoutEvent(
            product="my_activity",
            timestamp=datetime(2026, 5, 1, 12, tzinfo=UTC),
            title="Searched for duckdb performance",
            service="Search",
            source_member="fixture",
            payload={},
        )
    ]
    bookmarks = [
        BookmarkEvent(
            bookmark_id="b1",
            source="fixture",
            browser="firefox",
            profile="default",
            url="https://duckdb.org/docs",
            normalized_url="https://duckdb.org/docs",
            domain="duckdb.org",
            title="DuckDB docs",
            folder="dev",
            added_at=datetime(2026, 5, 2, 12, tzinfo=UTC),
            source_path="fixture",
        )
    ]
    web = [("duckdb.org", 5, 1.0)]

    report = personal_interest_trace(
        start=date(2026, 5, 1),
        google_events=google,
        bookmark_events=bookmarks,
        web_domain_rows=web,
        top_n=5,
    )
    payload = report.to_json()
    duckdb = next(row for row in payload["topics"] if row["topic"] == "duckdb")

    assert duckdb["sources"] == ["bookmarks", "google_takeout", "webhistory"]
    assert duckdb["active_days"] == 2
    assert duckdb["source_counts"]["webhistory"] == 5
    assert duckdb["score"] > 7


def test_personal_interest_trace_bounds_google_event_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lynchpin.analysis.personal_interest_fusion as fusion

    google_calls = []
    bookmark_calls = []

    def fake_iter_events(*, start=None, end=None, ensure=True):
        google_calls.append({"start": start, "end": end, "ensure": ensure})
        return iter(())

    def fake_iter_bookmarks(*, start=None, end=None, ensure=True):
        bookmark_calls.append({"start": start, "end": end, "ensure": ensure})
        return iter(())

    monkeypatch.setattr(fusion, "iter_events", fake_iter_events)
    monkeypatch.setattr(fusion, "iter_bookmarks", fake_iter_bookmarks)

    personal_interest_trace(
        start=date(2026, 5, 1),
        end=date(2026, 5, 4),
        web_domain_rows=(),
    )

    expected = [{"start": date(2026, 5, 1), "end": date(2026, 5, 4), "ensure": True}]
    assert google_calls == expected
    assert bookmark_calls == expected
