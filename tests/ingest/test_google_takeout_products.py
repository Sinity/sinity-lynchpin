from __future__ import annotations

import json
import zipfile
from datetime import date, datetime, timezone

from lynchpin.ingest import google_takeout_products
from lynchpin.ingest.google_takeout_products import GOOGLE_TAKEOUT_PRODUCTS_SCHEMA_VERSION
from lynchpin.sources import google_takeout_products as source


def test_materialize_google_takeout_products_ignores_calendar_export(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    archive = raw / "takeout.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "Takeout/Tasks/Tasks.json",
            json.dumps({
                "items": [
                    {
                        "id": "list-1",
                        "title": "Inbox",
                        "items": [
                            {
                                "id": "task-1",
                                "title": "pay invoice",
                                "status": "needsAction",
                                "created": "2026-01-01T00:00:00Z",
                            }
                        ],
                    }
                ]
            }),
        )
        zf.writestr(
            "Takeout/Keep/2026-01-01T00_00_00.000Z.json",
            json.dumps({
                "title": "note",
                "textContent": "body",
                "createdTimestampUsec": "1767225600000000",
                "userEditedTimestampUsec": "1767225600000000",
            }),
        )
        zf.writestr(
            "Takeout/My Activity/Search/MyActivity.html",
            '<div class="outer-cell"><div>Search</div><div>Searched for lynchpin</div>'
            "<div>Jan 1, 2026, 1:00:00 PM UTC</div></div>",
        )
        zf.writestr("Takeout/Calendar/example.ics", "BEGIN:VCALENDAR\nEND:VCALENDAR\n")

    cfg = type("Cfg", (), {"exports_root": tmp_path / "exports"})()
    calls = []
    monkeypatch.setattr(google_takeout_products, "get_config", lambda: cfg)
    monkeypatch.setattr(source, "get_config", lambda: cfg)
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: calls.append((name, window)),
    )

    manifest = google_takeout_products.materialize_google_takeout_products(root=raw)

    assert manifest["input_file_count"] == 1
    assert manifest["input_latest_mtime"] is not None
    assert manifest["schema_version"] == GOOGLE_TAKEOUT_PRODUCTS_SCHEMA_VERSION
    assert manifest["products"]["tasks"]["row_count"] == 1
    assert manifest["products"]["keep_notes"]["row_count"] == 1
    assert manifest["products"]["my_activity"]["row_count"] == 1
    assert manifest["first_date"] == "2026-01-01"
    assert manifest["last_date"] == "2026-01-01"
    assert manifest["products"]["tasks"]["first_date"] == "2026-01-01"
    assert manifest["products"]["tasks"]["last_date"] == "2026-01-01"
    assert manifest["products"]["my_activity"]["first_date"] == "2026-01-01"
    assert manifest["products"]["my_activity"]["last_date"] == "2026-01-01"
    assert "Calendar" not in manifest["supported_products"]
    assert "Calendar" not in manifest["skipped_products"]
    assert list(source.iter_tasks())[0]["title"] == "pay invoice"
    assert list(source.iter_keep_notes())[0]["title"] == "note"
    activity = list(source.iter_my_activity())[0]
    assert activity["title"] == "Searched for lynchpin"
    assert activity["timestamp"] == "2026-01-01T13:00:00+00:00"

    events = list(source.iter_events())
    assert {event.product for event in events} == {"keep_notes", "my_activity", "tasks"}
    daily = list(source.iter_daily_activity())
    assert any(row.product == "my_activity" and row.event_count == 1 for row in daily)
    assert calls
    assert set(calls) == {("google_takeout", None)}


def test_iter_gmail_reads_materialized_gmail_product(monkeypatch):
    from lynchpin.sources.gmail_takeout import GmailMessage

    def fail_raw_parse():
        raise AssertionError("iter_gmail must not reparse raw Gmail archives")

    monkeypatch.setattr(source, "iter_gmail_messages_deduped", fail_raw_parse, raising=False)
    monkeypatch.setattr(
        "lynchpin.sources.gmail_takeout.iter_materialized_gmail_messages",
        lambda **kwargs: iter((
            GmailMessage(
                message_id="<1@example.com>",
                thread_id="thread-1",
                sender="alice@example.com",
                recipients=("bob@example.com",),
                cc=(),
                timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
                subject="hello",
                body_preview="body",
                label="Mail",
                archive_source="fixture",
                size_bytes=10,
            ),
        )),
    )

    rows = list(source.iter_gmail())

    assert rows == [
        {
            "product": "gmail",
            "timestamp": "2026-01-01T12:00:00+00:00",
            "message_id": "<1@example.com>",
            "thread_id": "thread-1",
            "sender": "alice@example.com",
            "recipients": ["bob@example.com"],
            "cc": [],
            "subject": "hello",
            "body_preview": "body",
            "label": "Mail",
            "archive_source": "fixture",
            "size_bytes": 10,
        }
    ]


def test_iter_daily_activity_uses_single_windowed_materialization(monkeypatch):
    calls = []
    event = source.GoogleTakeoutEvent(
        product="tasks",
        timestamp=datetime(2026, 5, 5, 12, tzinfo=timezone.utc),
        title="task",
        service=None,
        source_member="fixture",
        payload={},
    )

    def fake_ensure(name, *, window=None):
        calls.append((name, window))

    def fake_events(product=None, *, ensure=True):
        assert product is None
        assert ensure is False
        yield event

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure)
    monkeypatch.setattr(source, "iter_events", fake_events)

    rows = list(source.iter_daily_activity(start=date(2026, 5, 5), end=date(2026, 5, 6)))

    assert calls == [("google_takeout", (date(2026, 5, 5), date(2026, 5, 6)))]
    assert rows[0].event_count == 1
