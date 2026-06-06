from __future__ import annotations

from datetime import datetime, timezone
import zipfile

from lynchpin.ingest import gmail_takeout_materialize
from lynchpin.ingest.gmail_takeout_materialize import GMAIL_EVENTS_SCHEMA_VERSION
from lynchpin.sources.gmail_takeout import GmailMessage


def test_materialize_gmail_events_writes_schema_and_input_high_water(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    archive = raw / "takeout.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Takeout/Mail/Mail.mbox", "")

    cfg = type("Cfg", (), {"exports_root": tmp_path / "exports"})()
    monkeypatch.setattr(gmail_takeout_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(
        gmail_takeout_materialize,
        "iter_gmail_messages_deduped",
        lambda *, root: iter((
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
                archive_source=str(archive),
                size_bytes=10,
            ),
        )),
    )

    manifest = gmail_takeout_materialize.materialize_gmail_events(root=raw)

    assert manifest["schema_version"] == GMAIL_EVENTS_SCHEMA_VERSION
    assert manifest["row_count"] == 1
    assert manifest["first_date"] == "2026-01-01"
    assert manifest["last_date"] == "2026-01-01"
    assert manifest["input_files"] == [str(archive)]
    assert manifest["input_file_count"] == 1
    assert manifest["input_latest_mtime"] is not None
