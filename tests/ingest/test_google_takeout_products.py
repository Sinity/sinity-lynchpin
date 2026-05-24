from __future__ import annotations

import json
import zipfile

from lynchpin.ingest import google_takeout_products
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
    monkeypatch.setattr(google_takeout_products, "get_config", lambda: cfg)
    monkeypatch.setattr(source, "get_config", lambda: cfg)

    manifest = google_takeout_products.materialize_google_takeout_products(root=raw)

    assert manifest["products"]["tasks"]["row_count"] == 1
    assert manifest["products"]["keep_notes"]["row_count"] == 1
    assert manifest["products"]["my_activity"]["row_count"] == 1
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
