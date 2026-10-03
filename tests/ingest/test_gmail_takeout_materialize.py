from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
import base64
import json
import zipfile
import pytest

from lynchpin.ingest import gmail_takeout_materialize
from lynchpin.ingest.gmail_takeout_materialize import GMAIL_EVENTS_SCHEMA_VERSION
from lynchpin.sources.gmail_takeout import GmailMessage, find_materialized_gmail_messages, iter_materialized_gmail_messages
from lynchpin.core.errors import MaterializationError, SourceUnavailableError


def test_materialize_gmail_events_writes_schema_and_input_high_water(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    archive = raw / "takeout.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Takeout/Mail/Mail.mbox", "")

    cfg = type("Cfg", (), {"accounts_root": tmp_path / "exports"})()
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


def _archive(path, *, body="", date="Mon, 21 Apr 2025 10:00:00 +0000", native_id=True):
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["Subject"] = "fixture"
    message["X-Gmail-Labels"] = "Inbox,Starred"
    if date is not None and date != "not a date":
        message["Date"] = date
    if native_id:
        message["Message-ID"] = "<native@example.com>"
    message.set_content(body)
    message.add_attachment(b"fixture attachment", maintype="application", subtype="octet-stream", filename="sample.bin")
    raw_message = message.as_bytes()
    if date == "not a date":
        raw_message = b"Date: not a date\n" + raw_message
    mbox = b"From sender@example.com Mon Apr 21 10:00:00 2025\n" + raw_message + b"\n"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Takeout/Mail/Mail.mbox", mbox)


def test_unreadable_input_preserves_last_good_and_complete_empty_can_publish(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    archive = raw / "takeout.zip"
    _archive(archive, body="retained body")
    output = tmp_path / "events.ndjson"
    monkeypatch.setattr(gmail_takeout_materialize, "gmail_events_path", lambda: output)
    monkeypatch.setattr(gmail_takeout_materialize, "gmail_manifest_path", lambda: output.with_suffix(".manifest.json"))
    gmail_takeout_materialize.materialize_gmail_events(root=raw)
    before = (output.read_bytes(), output.with_suffix(".manifest.json").read_bytes())
    archive.write_bytes(b"broken archive")
    with pytest.raises(SourceUnavailableError):
        gmail_takeout_materialize.materialize_gmail_events(root=raw)
    assert (output.read_bytes(), output.with_suffix(".manifest.json").read_bytes()) == before
    assert next(iter_materialized_gmail_messages(path=output, ensure=False)).body == "retained body\n"
    archive.unlink()
    result = gmail_takeout_materialize.materialize_gmail_events(root=raw)
    assert result["row_count"] == 0
    assert output.read_bytes() == b""


def test_override_and_mutation_bind_manifest_to_consumed_input(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    archive = raw / "takeout.zip"
    _archive(archive, body="first")
    canonical = tmp_path / "canonical.ndjson"
    monkeypatch.setattr(gmail_takeout_materialize, "gmail_events_path", lambda: canonical)
    monkeypatch.setattr(gmail_takeout_materialize, "gmail_manifest_path", lambda: canonical.with_suffix(".manifest.json"))
    first = gmail_takeout_materialize.materialize_gmail_events(root=raw)
    old = (canonical.read_bytes(), canonical.with_suffix(".manifest.json").read_bytes())
    other = tmp_path / "other.ndjson"
    gmail_takeout_materialize.materialize_gmail_events(root=raw, output=other)
    assert other.exists() and (canonical.read_bytes(), canonical.with_suffix(".manifest.json").read_bytes()) == old
    original = gmail_takeout_materialize.iter_gmail_messages_deduped

    def mutate_during_read(*, root):
        yield from original(root=root)
        _archive(archive, body="changed")

    monkeypatch.setattr(gmail_takeout_materialize, "iter_gmail_messages_deduped", mutate_during_read)
    with pytest.raises(MaterializationError, match="changed while reading"):
        gmail_takeout_materialize.materialize_gmail_events(root=raw)
    assert (canonical.read_bytes(), canonical.with_suffix(".manifest.json").read_bytes()) == old
    assert json.loads(old[1])["input_versions"] == first["input_versions"]


def test_exact_retained_search_finds_beyond_preview_and_unknown_identity(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    _archive(raw / "takeout.zip", body="a" * 250 + " precise answer", date="not a date", native_id=False)
    output = tmp_path / "events.ndjson"
    monkeypatch.setattr(gmail_takeout_materialize, "gmail_events_path", lambda: output)
    monkeypatch.setattr(gmail_takeout_materialize, "gmail_manifest_path", lambda: output.with_suffix(".manifest.json"))
    report = gmail_takeout_materialize.materialize_gmail_events(root=raw)
    hits = list(find_materialized_gmail_messages("precise answer", path=output, ensure=False))
    assert report["unknown_timestamp_count"] == 1
    assert len(hits) == 1 and "precise answer" not in hits[0].body_preview
    hit = hits[0]
    assert hit.body and "precise answer" in hit.body
    assert hit.native_message_id is None and hit.message_id.startswith("sha256:")
    assert hit.date_status == "invalid" and hit.timestamp is None
    assert hit.native_labels == ("Inbox", "Starred")
    assert hit.archive_member == "Takeout/Mail/Mail.mbox" and hit.occurrence_index == 0
    assert hit.mime_parts[-1]["filename"] == "sample.bin"
    assert hit.raw_message_base64 and b"fixture attachment" not in base64.b64decode(hit.raw_message_base64)
    assert b"sample.bin" in base64.b64decode(hit.raw_message_base64)
    assert list(find_materialized_gmail_messages("absent content", path=output, ensure=False)) == []


def test_gmail_parser_serializes_non_ascii_raw_headers() -> None:
    from lynchpin.sources.gmail_takeout import _parse_mbox_bytes

    raw = (b"From sender@example.com Mon Apr 21 10:00:00 2025\n"
           b"From: sender@example.com\nSubject: fixture \xff\n"
           b"Content-Type: text/plain\n\nneutral body\n")
    message = next(_parse_mbox_bytes(raw, "Mail", "fixture.zip", "Mail.mbox"))
    payload = gmail_takeout_materialize._message_payload(message)
    assert all(isinstance(value, str) for _name, value in payload["headers"])
    json.dumps(payload)
