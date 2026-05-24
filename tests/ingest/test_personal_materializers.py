from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

from lynchpin.ingest.bookmarks_materialize import materialize_bookmarks
from lynchpin.ingest.communications_materialize import materialize_communication_events
from lynchpin.ingest.personal_signals_materialize import materialize_spotify_daily
from lynchpin.sources.bookmarks import iter_bookmarks
from lynchpin.sources.communications import iter_communication_events
from lynchpin.sources.personal_signals import iter_spotify_daily_signals


def test_materialize_bookmarks_reads_chromium_and_firefox(monkeypatch, tmp_path):
    root = tmp_path / "bookmarks"
    chrome = root / "historical" / "machine"
    chrome.mkdir(parents=True)
    (chrome / "chrome_bookmarks.json").write_text(
        json.dumps(
            {
                "roots": {
                    "bookmark_bar": {
                        "type": "folder",
                        "children": [
                            {
                                "type": "url",
                                "name": "Example",
                                "url": "https://example.com/",
                                "date_added": "13228166792370662",
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    firefox = root / "historical" / "ff"
    firefox.mkdir(parents=True)
    db = firefox / "places.sqlite"
    con = sqlite3.connect(db)
    con.executescript(
        """
        create table moz_places(id integer primary key, url text, title text);
        create table moz_bookmarks(id integer primary key, fk integer, type integer, parent integer, title text, dateAdded integer);
        insert into moz_places values(1, 'https://mozilla.org/', 'Mozilla');
        insert into moz_bookmarks values(10, null, 2, 0, 'menu', 0);
        insert into moz_bookmarks values(11, 1, 1, 10, 'Mozilla', 1624545525000000);
        """
    )
    con.close()
    cfg = type("Cfg", (), {"browser_bookmarks_root": root, "libraries_root": tmp_path / "libraries"})()
    monkeypatch.setattr("lynchpin.ingest.bookmarks_materialize.get_config", lambda: cfg)
    monkeypatch.setattr("lynchpin.sources.bookmarks.get_config", lambda: cfg)

    manifest = materialize_bookmarks(root=root)
    rows = list(iter_bookmarks(root / "processed/bookmarks.ndjson"))

    assert manifest["row_count"] == 2
    assert {row.browser for row in rows} == {"chrome", "firefox"}
    assert {row.domain for row in rows} == {"example.com", "mozilla.org"}


def test_materialize_communications_reads_outlook_csv(monkeypatch, tmp_path):
    exports = tmp_path / "exports"
    outlook = exports / "comms" / "outlook" / "raw"
    outlook.mkdir(parents=True)
    csv_path = outlook / "sent.CSV"
    with csv_path.open("w", encoding="cp1250", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Temat",
                "Treść",
                "Od: (imię/nazwisko)",
                "Od: (adres)",
                "Do: (imię/nazwisko)",
                "Do: (adres)",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Temat": "Build",
                "Treść": "Sent: Mon, 06 Sep 2021 10:16:00 +0200\nBody",
                "Od: (imię/nazwisko)": "Me",
                "Od: (adres)": "me@example.com",
                "Do: (imię/nazwisko)": "You",
                "Do: (adres)": "you@example.com",
            }
        )
    cfg = type(
        "Cfg",
        (),
        {
            "exports_root": exports,
            "libraries_root": tmp_path / "libraries",
            "teams_root": tmp_path / "teams",
            "fbmessenger_gdpr_root": tmp_path / "messenger",
            "fbmessenger_db": tmp_path / "messenger.sqlite",
        },
    )()
    monkeypatch.setattr("lynchpin.ingest.communications_materialize.get_config", lambda: cfg)
    monkeypatch.setattr("lynchpin.sources.communications.get_config", lambda: cfg)

    manifest = materialize_communication_events()
    rows = list(iter_communication_events(exports / "comms/processed/communication_events.ndjson"))

    assert manifest["row_count"] == 1
    assert rows[0].source == "outlook"
    assert rows[0].direction == "outbound"
    assert rows[0].timestamp is not None


def test_materialize_spotify_daily_product(monkeypatch, tmp_path):
    streams = [
        SimpleNamespace(
            end_time=datetime(2026, 5, 1, 10, tzinfo=timezone.utc),
            artist="A",
            track="One",
            ms_played=60_000,
        ),
        SimpleNamespace(
            end_time=datetime(2026, 5, 1, 11, tzinfo=timezone.utc),
            artist="A",
            track="Two",
            ms_played=120_000,
        ),
    ]
    monkeypatch.setattr("lynchpin.sources.spotify.iter_streams", lambda: iter(streams))

    output = tmp_path / "derived" / "spotify" / "daily.ndjson"
    manifest = materialize_spotify_daily(output=output)
    rows = list(iter_spotify_daily_signals(output))

    assert manifest["row_count"] == 1
    assert rows[0].date.isoformat() == "2026-05-01"
    assert rows[0].track_count == 2
    assert rows[0].minutes_played == 3.0
    assert rows[0].top_artists == ("A",)
