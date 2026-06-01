from __future__ import annotations

from datetime import datetime, timezone

from lynchpin.analysis.google_takeout_mining import google_takeout_retrospective
from lynchpin.sources.google_takeout_products import GoogleTakeoutEvent


UTC = timezone.utc


def _event(
    day: int,
    hour: int,
    title: str,
    *,
    product: str = "my_activity",
    service: str | None = "Search",
) -> GoogleTakeoutEvent:
    return GoogleTakeoutEvent(
        product=product,
        timestamp=datetime(2026, 5, day, hour, 0, tzinfo=UTC),
        title=title,
        service=service,
        source_member="fixture",
        payload={"title": title},
    )


def test_google_takeout_retrospective_mines_search_sessions_and_anomalies() -> None:
    events = [
        _event(1, 10, "Searched for\xa0duckdb wal"),
        _event(1, 10, "Searched for duckdb wal"),
        _event(1, 11, "Opened docs", service="Docs"),
        _event(2, 10, "Searched for nix flakes"),
        *[_event(3, 9, f"Searched for burst {i}") for i in range(12)],
        _event(4, 12, "Calendar event", product="calendar", service=None),
    ]

    report = google_takeout_retrospective(source_events=events, top_n=10)
    payload = report.to_json()

    assert report.event_count == 17
    assert report.active_days == 4
    assert payload["search_terms"][0]["term"] == "duckdb wal"
    assert payload["search_terms"][0]["count"] == 2
    assert payload["sessions"][0]["event_count"] == 12
    assert payload["anomaly_days"][0]["date"] == "2026-05-03"
    assert payload["monthly_patterns"][0]["month"] == "2026-05"
    assert any(
        row["left"] == "Docs" and row["right"] == "Search"
        for row in payload["cooccurrences"]
    )
