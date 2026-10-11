import csv
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from lynchpin.ingest.exports_materialize import (
    REDDIT_CANONICAL_SCHEMA_VERSION,
    SPOTIFY_STREAMS_SCHEMA_VERSION,
    _export_roots,
    _row_date_bounds,
    _spotify_roots,
    _write_manifest,
    _write_reddit_manifest,
)


def test_raindrop_older_export_cannot_replace_newer_observation(tmp_path, monkeypatch):
    from lynchpin.ingest import exports_materialize as mod
    from lynchpin.sources import exports_raindrop as source

    raw = tmp_path / "raw"
    raw.mkdir()
    cfg = SimpleNamespace(accounts_root=tmp_path / "accounts", raindrop_dir=raw, raindrop_csv=None)
    monkeypatch.setattr(mod, "get_config", lambda: cfg)
    monkeypatch.setattr(source, "get_config", lambda: cfg)

    def export(name, timestamp, rows):
        path = raw / name
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("id", "url", "title", "tags", "created"))
            writer.writeheader()
            writer.writerows(rows)
        os.utime(path, (timestamp, timestamp))
        return path

    newer = export("new.csv", 200, [{"id": 7, "url": "https://example.org/current", "title": "new", "tags": "current", "created": "2026-08-01"}])
    mod.materialize_raindrop()
    before = mod.raindrop_bookmarks_path().read_bytes()
    older = export("old.csv", 100, [{"id": 7, "url": "https://example.org/current", "title": "old", "tags": "old", "created": "2026-08-01"}])
    report = mod.materialize_raindrop()
    assert mod.raindrop_bookmarks_path().read_bytes() == before
    assert set(report["input_files"]) == {str(newer), str(older)}
    row = list(source.iter_raindrop_bookmarks(ensure=False))[0]
    assert (row.title, row.tags, row.raw["source_file"]) == ("new", ["current"], str(newer))

    # CSV exports preserve historical identity/URL observations. A changed
    # URL remains attributable; absence in a later CSV is not a deletion event.
    export("old.csv", 100, [
        {"id": 7, "url": "https://example.org/previous", "title": "previous"},
        {"id": 8, "url": "https://example.org/retained", "title": "retained"},
    ])
    mod.materialize_raindrop()
    rows = list(source.iter_raindrop_bookmarks(ensure=False))
    assert {(row.id, row.url) for row in rows} == {
        (7, "https://example.org/current"), (7, "https://example.org/previous"), (8, "https://example.org/retained"),
    }
    assert all(row.raw["source_file"] == str(older) for row in rows if row.url != "https://example.org/current")


def test_raindrop_creation_counts_preserve_identity_across_url_history(tmp_path, monkeypatch):
    from lynchpin.sources import exports_raindrop as source
    path = tmp_path / "bookmarks.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("id", "url", "created", "tags"))
        writer.writeheader()
        writer.writerows([
            {"id": 17, "url": "https://example.org/old", "created": "2026-08-01T12:00:00Z", "tags": "one"},
            {"id": 17, "url": "https://example.org/new", "created": "2026-08-01T12:00:00Z", "tags": "two"},
        ])
    monkeypatch.setattr(source, "raindrop_bookmarks_path", lambda: path)
    assert source.summarize_raindrop_bookmarks("2026-08", "2026-08", csv_path=path) == {"2026-08": 1}
    rows = source.daily_raindrop_activity(start=date(2026, 8, 1), end=date(2026, 8, 2), ensure=False)
    assert len(rows) == 1 and rows[0].bookmarks_added == 1 and rows[0].unique_tags == 2
    assert len(list(source.iter_raindrop_bookmarks(csv_path=path))) == 2


def test_raindrop_ordinary_reader_adopts_complete_api_bundle(tmp_path, monkeypatch):
    from lynchpin.ingest import exports_materialize as mod
    from lynchpin.sources import exports_raindrop as source
    raw = tmp_path / "raw"
    raw.mkdir()
    cfg = SimpleNamespace(accounts_root=tmp_path / "accounts", raindrop_dir=raw, raindrop_csv=None)
    monkeypatch.setattr(mod, "get_config", lambda: cfg)
    monkeypatch.setattr(source, "get_config", lambda: cfg)
    snapshot = raw / "2026-08-02-api-export"
    snapshot.mkdir()
    metadata = {"_id": 17, "link": "https://example.org/bookmark", "title": "retained", "created": "2026-01-01T12:00:00Z", "lastUpdate": "2026-08-01T12:00:00Z", "tags": ["one, two"], "custom": {"full": True}}
    highlight = {"_id": "highlight-a", "raindropRef": 17, "text": "retained text"}
    for name in ("collections-root.json", "collections-children.json", "tags.json"):
        (snapshot / name).write_text('{}')
    for name, rows in (("raindrops-all.jsonl", [metadata]), ("raindrops-trash.jsonl", []), ("highlights.jsonl", [highlight])):
        (snapshot / name).write_text("".join(json.dumps(row) + "\n" for row in rows))
    (snapshot / "EXPORT-MANIFEST.json").write_text(json.dumps({"active": 1, "trash": 0, "highlights": 1, "exported_at": "2026-08-02T00:00:00Z"}))
    report = mod.materialize_raindrop()
    rows = list(source.iter_raindrop_bookmarks(ensure=False))
    assert len(rows) == 1
    row = rows[0]
    assert row.id == 17 and row.updated == datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    assert row.state == "active" and row.tags == ["one, two"]
    assert row.highlights == (highlight,)
    assert all(row.metadata[key] == value for key, value in metadata.items())
    assert set(report["input_files"]) == {str(path) for path in snapshot.iterdir()}
    # Newer CSV details win, while native evidence remains dated separately.
    csv_path = raw / "new.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("id", "url", "title", "created"))
        writer.writeheader()
        writer.writerow({"id": 17, "url": metadata["link"], "title": "new CSV", "created": metadata["created"]})
    mod.materialize_raindrop()
    row = list(source.iter_raindrop_bookmarks(ensure=False))[0]
    assert row.title == "new CSV" and row.metadata["title"] == "retained"
    assert row.raw["source_file"] == str(csv_path) and row.raw["api_source"] == str(snapshot)
    assert row.highlights == (highlight,)
    before = mod.raindrop_bookmarks_path().read_bytes()
    (snapshot / "highlights.jsonl").write_text("{interrupted")
    import pytest
    from lynchpin.core.errors import SourceUnavailableError
    with pytest.raises(SourceUnavailableError):
        mod.materialize_raindrop()
    assert mod.raindrop_bookmarks_path().read_bytes() == before


def test_communications_materialize_includes_themotte(tmp_path, monkeypatch) -> None:
    from lynchpin.ingest import communications_materialize
    from lynchpin.sources.themotte import TheMotteMessage, TheMotteNotification

    output = tmp_path / "communication_events.ndjson"
    manifest = tmp_path / "communication_events.manifest.json"
    cfg = type(
        "Cfg",
        (),
        {
            "accounts_root": tmp_path / "accounts",
            "teams_root": tmp_path / "teams",
            "themotte_root": tmp_path / "themotte",
            "themotte_username": "Sinity",
        },
    )()
    monkeypatch.setattr(communications_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(communications_materialize, "communication_manifest_path", lambda: manifest)
    monkeypatch.setattr(
        communications_materialize,
        "themotte_input_files",
        lambda *, root, username: (),
    )
    monkeypatch.setattr(
        communications_materialize,
        "iter_themotte_messages",
        lambda *, root, username: iter(
            (
                TheMotteMessage(
                    id="1",
                    created_at=datetime(2026, 2, 1, 10, tzinfo=timezone.utc),
                    author="self_made_human",
                    recipient="Sinity",
                    peer="self_made_human",
                    body="reply",
                    url="https://www.themotte.org/comment/1",
                    relative_time="4mo ago",
                ),
            )
        ),
    )
    monkeypatch.setattr(
        communications_materialize,
        "iter_themotte_notifications",
        lambda *, root, username: iter(
            (
                TheMotteNotification(
                    id="n1",
                    created_at=datetime(2026, 2, 1, 11, tzinfo=timezone.utc),
                    kind="Username Mention",
                    actor="naraburns",
                    title="Username Mention",
                    text="mentioned @Sinity",
                    url="https://www.themotte.org/comment/2",
                    relative_time="4mo ago",
                    unread=True,
                ),
            )
        ),
    )

    report = communications_materialize.materialize_communication_events(output=output)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    assert report["sources"] == ["themotte"]
    assert [row["raw_kind"] for row in rows] == ["themotte_private_message", "themotte_notification"]
    assert rows[0]["direction"] == "inbound"


def test_communications_identity_uses_full_semantics_and_daily_counts(
    tmp_path: Path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from lynchpin.ingest import communications_materialize
    from lynchpin.sources.communications import daily_communication_activity

    accounts = tmp_path / "accounts"
    messenger_path = accounts / "facebook-messenger/processed/canonical/messages.ndjson"
    messenger_path.parent.mkdir(parents=True)
    messenger_path.write_text("fixture\n", encoding="utf-8")
    output = tmp_path / "communication_events.ndjson"
    manifest = tmp_path / "communication_events.manifest.json"
    cfg = SimpleNamespace(
        accounts_root=accounts,
        teams_root=tmp_path / "teams",
        themotte_root=tmp_path / "themotte",
        themotte_username="operator",
    )

    def message(*, day: int, **changes):
        values = {
            "thread_name": "conversation",
            "participants": ["sender", "recipient"],
            "sender": "sender",
            "timestamp": datetime(2026, 2, day, 10, tzinfo=timezone.utc),
            "text": "",
            "kind": "message",
            "media_count": 0,
            "source": f"/raw/{day}/default",
        }
        values.update(changes)
        return SimpleNamespace(**values)

    long_prefix = "x" * 240
    messages = [
        message(day=1, text=long_prefix + " alpha", source="/raw/long/alpha"),
        message(
            day=1,
            text=long_prefix + " alpha",
            timestamp=datetime(2026, 2, 1, 12, tzinfo=timezone(timedelta(hours=2))),
            participants=["recipient", "sender"],
            source="/raw/long/duplicate",
        ),
        message(day=1, text=long_prefix + " alpha", source="/raw/long/raw-path-only"),
        message(day=1, text=long_prefix + " bravo", source="/raw/long/bravo"),
        message(day=5, text="timestamp identity", timestamp=None, source="/raw/timestamp/null"),
        message(day=5, text="timestamp identity", source="/raw/timestamp/dated"),
        message(day=2, media_count=1, source="/raw/media/one"),
        message(day=2, media_count=2, source="/raw/media/two"),
        message(day=3, kind="message", source="/raw/kind/message"),
        message(day=3, kind="reaction", source="/raw/kind/reaction"),
        message(day=4, participants=["sender", "recipient-a"], source="/raw/recipient/a"),
        message(day=4, participants=["sender", "recipient-b"], source="/raw/recipient/b"),
    ]

    monkeypatch.setattr(communications_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(communications_materialize, "communication_manifest_path", lambda: manifest)
    monkeypatch.setattr(communications_materialize, "themotte_input_files", lambda **_kwargs: ())
    monkeypatch.setattr(
        communications_materialize,
        "iter_fbmessenger_messages",
        lambda **_kwargs: iter(messages),
    )
    monkeypatch.setattr(
        "lynchpin.sources.communications.communication_events_path",
        lambda root=None: output,
    )

    result = communications_materialize.materialize_communication_events(output=output)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    by_path = {row["raw_path"]: row for row in rows}
    daily = daily_communication_activity(
        start=date(2026, 2, 1),
        end=date(2026, 2, 6),
        ensure=False,
    )

    assert result["schema_version"] == communications_materialize.COMMUNICATION_EVENTS_SCHEMA_VERSION == 2
    assert result["row_count"] == 10
    assert len(rows) == len({row["event_id"] for row in rows})
    assert by_path["/raw/long/alpha"]["event_id"] != by_path["/raw/long/bravo"]["event_id"]
    assert "/raw/long/duplicate" not in by_path
    assert "/raw/long/raw-path-only" not in by_path
    assert by_path["/raw/media/one"]["event_id"] != by_path["/raw/media/two"]["event_id"]
    assert by_path["/raw/kind/message"]["event_id"] != by_path["/raw/kind/reaction"]["event_id"]
    assert by_path["/raw/recipient/a"]["event_id"] != by_path["/raw/recipient/b"]["event_id"]
    assert by_path["/raw/recipient/a"]["recipients"] != by_path["/raw/recipient/b"]["recipients"]
    assert by_path["/raw/timestamp/null"]["timestamp"] is None
    assert by_path["/raw/timestamp/null"]["event_id"] != by_path["/raw/timestamp/dated"]["event_id"]
    assert [(row.date.isoformat(), row.event_count) for row in daily] == [
        ("2026-02-01", 2),
        ("2026-02-02", 2),
        ("2026-02-03", 2),
        ("2026-02-04", 2),
        ("2026-02-05", 1),
    ]


def test_export_roots_only_accept_dated_directories(tmp_path: Path) -> None:
    dated = tmp_path / "2026-01-02"
    dated.mkdir()
    for name in ("legacy", "raw", "canonical", "not-an-export"):
        (tmp_path / name).mkdir()

    assert _export_roots(tmp_path) == [dated]


def test_spotify_roots_do_not_descend_into_legacy_staging(tmp_path: Path) -> None:
    dated = tmp_path / "2026-01-02"
    (dated / "Spotify Account Data").mkdir(parents=True)
    legacy = tmp_path / "legacy"
    (legacy / "Spotify Account Data").mkdir(parents=True)

    assert _spotify_roots(tmp_path) == [dated]


def test_export_manifest_records_input_high_water(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text("[]", encoding="utf-8")
    product = tmp_path / "product.ndjson"
    product.write_text("{}\n", encoding="utf-8")
    manifest_path = tmp_path / "product.manifest.json"

    manifest = _write_manifest(
        manifest_path,
        "fixture.dataset",
        [{"created": "2026-01-02T03:04:05+00:00", "source_file": str(source)}],
        product_path=product,
        source_files=(source,),
        schema_version=SPOTIFY_STREAMS_SCHEMA_VERSION,
        extra={"thread_count": 3},
    )

    assert manifest["schema_version"] == SPOTIFY_STREAMS_SCHEMA_VERSION
    assert manifest["input_file_count"] == 1
    assert manifest["input_files"] == [str(source)]
    assert manifest["input_latest_mtime"] is not None
    assert manifest["first_date"] == "2026-01-02"
    assert manifest["thread_count"] == 3
    assert '"thread_count": 3' in manifest_path.read_text(encoding="utf-8")
    assert manifest_path.exists()


def test_messenger_materialization_preserves_native_occurrences_and_reads_them(
    tmp_path: Path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from lynchpin.ingest import exports_materialize
    from lynchpin.sources.exports_messenger import iter_fbmessenger_messages

    raw = tmp_path / "gdpr"
    sources = []
    for folder, thread_id, body in (
        ("one", "conversation-1", "same title"),
        ("two", "conversation-2", "same title"),
        ("duplicate", "conversation-1", "same title"),
    ):
        directory = raw / folder / "messages"
        directory.mkdir(parents=True)
        source = directory / "message_1.json"
        source.write_text(
            json.dumps({
                "threadId": thread_id,
                "threadName": body,
                "participants": [{"name": "Alice"}],
                "messages": [
                    {"senderName": "Alice", "timestamp": 1770000000000, "text": "repeat"},
                    {"senderName": "Alice", "timestamp": 1770000000000, "text": "repeat"},
                    {"senderName": "Alice", "timestamp": 1770000001000, "photos": [{}]},
                ],
            }),
            encoding="utf-8",
        )
        if folder == "one":
            sources.append(source)
    accounts = tmp_path / "accounts"
    monkeypatch.setattr(exports_materialize, "get_config", lambda: SimpleNamespace(
        accounts_root=accounts, fbmessenger_gdpr_root=raw
    ))

    exports_materialize.materialize_messenger()
    threads = [json.loads(row) for row in (accounts / "facebook-messenger/processed/canonical/threads.ndjson").read_text().splitlines()]
    messages_path = accounts / "facebook-messenger/processed/canonical/messages.ndjson"
    messages = list(iter_fbmessenger_messages(paths=[messages_path], ensure=False))
    before = messages_path.read_text(encoding="utf-8")
    exports_materialize.materialize_messenger()

    assert len(threads) == 2, "same display title must not merge native conversations"
    assert len(messages) == 6, "identical text and media-only source positions must remain occurrences"
    assert {thread["thread_id"] for thread in threads} == {"conversation-1", "conversation-2"}
    assert len({message.source_locator for message in messages}) == 6
    assert messages_path.read_text(encoding="utf-8") == before, "unchanged reimport must be idempotent"

    revised = json.loads(sources[0].read_text(encoding="utf-8"))
    revised["messages"][0]["text"] = "revised"
    sources[0].write_text(json.dumps(revised), encoding="utf-8")
    exports_materialize.materialize_messenger()
    revised_messages = list(iter_fbmessenger_messages(paths=[messages_path], ensure=False))
    assert len(revised_messages) == 6, "a revised occurrence must replace its prior locator"
    assert sum(message.text == "revised" for message in revised_messages) == 1


def test_reddit_manifest_records_aggregate_bounds(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("date\n2026-01-01\n", encoding="utf-8")
    manifest_path = tmp_path / "reddit" / "manifest.json"
    manifest_path.parent.mkdir()

    manifest = _write_reddit_manifest(
        manifest_path,
        {
            "comments.csv": {
                "row_count": 2,
                "first_date": "2026-01-03",
                "last_date": "2026-01-04",
            },
            "posts.csv": {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-02",
            },
        },
        product_path=manifest_path.parent,
        source_files=(source,),
    )

    assert manifest["schema_version"] == REDDIT_CANONICAL_SCHEMA_VERSION
    assert manifest["row_count"] == 3
    assert manifest["first_date"] == "2026-01-01"
    assert manifest["last_date"] == "2026-01-04"
    assert manifest["input_files"] == [str(source)]
    assert manifest["input_file_count"] == 1


def test_reddit_style_utc_dates_contribute_to_bounds() -> None:
    assert _row_date_bounds(
        (
            {"date": "2013-10-19 00:03:30 UTC"},
            {"date": "2014-02-13 15:06:36 UTC"},
        )
    ) == ("2013-10-19", "2014-02-13")


def test_reddit_materializer_merges_arctic_shift_content(tmp_path: Path, monkeypatch) -> None:
    from lynchpin.ingest import exports_materialize

    processed = tmp_path / "reddit" / "processed"
    dated = processed / "2026-01-01"
    dated.mkdir(parents=True)
    (dated / "comments.csv").write_text(
        "id,permalink,date,subreddit,body\nc1,/r/x/c1,2020-01-01 00:00:00 UTC,x,old\n",
        encoding="utf-8",
    )
    canonical = processed / "canonical"
    canonical.mkdir()
    (canonical / "comments.csv").write_text(
        "id,permalink,date,subreddit,body\nstale,/r/old,2010-01-01 UTC,old,stale\n",
        encoding="utf-8",
    )
    arctic = tmp_path / "reddit" / "arctic-shift" / "2026-08-11"
    arctic.mkdir(parents=True)
    (arctic / "comments.jsonl").write_text(
        '{"id":"c1","permalink":"/r/x/c1","created_utc":1577836800,"subreddit":"x","body":"old"}\n'
        '{"id":"c2","permalink":"/r/y/c2","created_utc":1577923200,"subreddit":"y","body":"new"}\n',
        encoding="utf-8",
    )
    cfg = type("Cfg", (), {"accounts_root": tmp_path})()
    monkeypatch.setattr(exports_materialize, "get_config", lambda: cfg)

    report = exports_materialize.materialize_reddit()
    with (processed / "canonical" / "comments.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert [row["id"] for row in rows] == ["c1", "c2"]
    assert rows[0]["permalink"] == "https://www.reddit.com/r/x/c1"
    assert report["files"]["comments.csv"]["row_count"] == 2
    assert str(arctic / "comments.jsonl") in (processed / "canonical" / "manifest.json").read_text()


def test_export_carrier_preserves_surrogate_native_locator(tmp_path: Path) -> None:
    from lynchpin.ingest.exports_materialize import _write_ndjson

    output = tmp_path / "messages.ndjson"
    row = {"source_locator": "fixture\udce1:message:0", "text": "neutral"}
    _write_ndjson(output, [row])
    assert json.loads(output.read_text(encoding="utf-8")) == row
