from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.sources import web
from lynchpin.sources.web_models import WebHistoryEntry, WebHistoryVisit


def test_iter_entries_uses_bounded_canonical_visits(monkeypatch, tmp_path) -> None:
    # Anti-vacuity: fails if the row's provenance is reduced to a path
    # component ("Profile 1") or the canonical file is reported as the source.
    calls = []
    canonical = tmp_path / "full_history.ndjson"
    monkeypatch.setattr(web, "get_config", lambda: SimpleNamespace(webhistory_ndjson=canonical))
    visits = [
        WebHistoryVisit(
            timestamp=datetime(2026, 5, 2, 12, tzinfo=timezone.utc),
            url="https://example.com/a",
            title="A",
            source="live_profile:chrome-ws/Profile 1",
        )
    ]

    def fake_iter_all_visits(*, start=None, end=None, ensure=True):
        calls.append((start, end, ensure))
        return iter(visits)

    monkeypatch.setattr(web, "_iter_all_visits", fake_iter_all_visits)

    rows = list(web.iter_entries(start=date(2026, 5, 2), end=date(2026, 5, 3)))

    assert calls == [(date(2026, 5, 2), date(2026, 5, 3), True)]
    assert rows == [
        {
            "url": "https://example.com/a",
            "title": "A",
            "iso_time": "2026-05-02T12:00:00+00:00",
            "source": "live_profile:chrome-ws/Profile 1",
            "_source_file": str(canonical),
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
            start=date(2026, 5, 2),
            end=date(2026, 5, 2),
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
