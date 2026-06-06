from __future__ import annotations

from datetime import datetime, timezone

from lynchpin.sources import web
from lynchpin.sources.web_models import WebHistoryEntry, WebHistoryVisit


def test_iter_entries_uses_bounded_canonical_visits(monkeypatch) -> None:
    calls = []
    visits = [
        WebHistoryVisit(
            timestamp=datetime(2026, 5, 2, 12, tzinfo=timezone.utc),
            url="https://example.com/a",
            title="A",
            source="/tmp/full_history.ndjson",
        )
    ]

    def fake_iter_all_visits(*, start=None, end=None, ensure=True):
        calls.append((start, end, ensure))
        return iter(visits)

    monkeypatch.setattr(web, "_iter_all_visits", fake_iter_all_visits)

    rows = list(web.iter_entries(start_date="2026-05-02", end_date="2026-05-03"))

    assert calls == [(datetime(2026, 5, 2).date(), datetime(2026, 5, 3).date(), True)]
    assert rows == [
        {
            "url": "https://example.com/a",
            "title": "A",
            "iso_time": "2026-05-02T12:00:00+00:00",
            "source": "full_history.ndjson",
            "_source_file": "/tmp/full_history.ndjson",
        }
    ]


def test_iter_entries_preserves_explicit_legacy_loader(monkeypatch, tmp_path) -> None:
    legacy = WebHistoryEntry(
        date="2026-05-02",
        record_json='{"url": "https://legacy.example", "title": "Legacy"}',
        source_file="/tmp/raw.jsonl",
    )
    monkeypatch.setattr(web, "_load_entries", lambda root=None, ndjson=None: [legacy])

    rows = list(
        web.iter_entries(
            start_date="2026-05-02",
            end_date="2026-05-02",
            root=tmp_path,
        )
    )

    assert rows == [
        {
            "url": "https://legacy.example",
            "title": "Legacy",
            "_source_file": "/tmp/raw.jsonl",
        }
    ]
