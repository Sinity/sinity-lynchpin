from __future__ import annotations

import csv
import inspect
import json
import sqlite3
import re
from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.ingest.bookmarks_materialize import (
    BOOKMARK_EVENTS_SCHEMA_VERSION,
    _discover_bookmark_files,
    materialize_bookmarks,
)
from lynchpin.ingest.communications_materialize import (
    COMMUNICATION_EVENTS_SCHEMA_VERSION,
    materialize_communication_events,
)
from lynchpin.ingest.personal_signals_materialize import (
    PERSONAL_DAILY_SIGNALS_SCHEMA_VERSION,
    SPOTIFY_DAILY_SCHEMA_VERSION,
    materialize_personal_daily_signals,
    materialize_spotify_daily,
)
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
    assert manifest["schema_version"] == BOOKMARK_EVENTS_SCHEMA_VERSION
    assert manifest["input_file_count"] == 2
    assert manifest["input_latest_mtime"] is not None
    assert {row.browser for row in rows} == {"chrome", "firefox"}
    assert {row.domain for row in rows} == {"example.com", "mozilla.org"}


def test_discover_bookmark_files_finds_supported_names_once(tmp_path):
    root = tmp_path / "bookmarks"
    nested = root / "profile"
    nested.mkdir(parents=True)
    expected = {
        nested / "chrome_bookmarks.json",
        nested / "Bookmarks",
        nested / "DefaultBookmarks.bak",
        nested / "places.sqlite",
        nested / "bookmarks.html",
        nested / "bookmarks-2026.jsonlz4",
    }
    for path in expected | {nested / "not-bookmarks.json", nested / "Bookmarks.txt"}:
        path.write_text("fixture", encoding="utf-8")

    assert set(_discover_bookmark_files((root,))) == expected


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
    assert manifest["schema_version"] == COMMUNICATION_EVENTS_SCHEMA_VERSION
    assert manifest["input_file_count"] == 1
    assert manifest["input_latest_mtime"] is not None
    assert rows[0].source == "outlook"
    assert rows[0].direction == "outbound"
    assert rows[0].timestamp is not None


def test_materialize_spotify_daily_product(monkeypatch, tmp_path):
    streams = [
        SimpleNamespace(
            end_time=datetime(2026, 5, 1, 3, 30, tzinfo=timezone.utc),
            artist="A",
            track="One",
            ms_played=60_000,
        ),
        SimpleNamespace(
            end_time=datetime(2026, 5, 1, 3, 45, tzinfo=timezone.utc),
            artist="A",
            track="Two",
            ms_played=120_000,
        ),
    ]
    calls = []

    def iter_streams(*, start=None, end=None):
        calls.append((start, end))
        return iter(streams)

    monkeypatch.setattr("lynchpin.sources.spotify.iter_streams", iter_streams)
    spotify_streams = tmp_path / "streaming_history.ndjson"
    spotify_streams.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "lynchpin.ingest.personal_signals_materialize.spotify_daily_input_files",
        lambda: (spotify_streams,),
    )

    output = tmp_path / "derived" / "spotify" / "daily.ndjson"
    manifest = materialize_spotify_daily(output=output)
    rows = list(iter_spotify_daily_signals(output))

    assert calls == [(None, None)]
    assert manifest["row_count"] == 1
    assert manifest["schema_version"] == SPOTIFY_DAILY_SCHEMA_VERSION
    assert manifest["input_file_count"] == 1
    assert manifest["input_latest_mtime"] is not None
    assert manifest["covered_dates"] == ["2026-04-30"]
    assert rows[0].date.isoformat() == "2026-04-30"
    assert rows[0].track_count == 2
    assert rows[0].minutes_played == 3.0
    assert rows[0].top_artists == ("A",)


def test_materialize_spotify_daily_merges_requested_window(monkeypatch, tmp_path):
    streams = [
        SimpleNamespace(
            end_time=datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc),
            artist="B",
            track="New",
            ms_played=180_000,
        ),
    ]
    calls = []

    def iter_streams(*, start=None, end=None):
        calls.append((start, end))
        return iter(streams)

    monkeypatch.setattr("lynchpin.sources.spotify.iter_streams", iter_streams)
    spotify_streams = tmp_path / "streaming_history.ndjson"
    spotify_streams.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "lynchpin.ingest.personal_signals_materialize.spotify_daily_input_files",
        lambda: (spotify_streams,),
    )

    output = tmp_path / "derived" / "spotify" / "daily.ndjson"
    output.parent.mkdir(parents=True)
    output.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "date": "2026-05-01",
                        "track_count": 1,
                        "minutes_played": 1.0,
                        "unique_artists": 1,
                        "unique_tracks": 1,
                        "top_artists": ["A"],
                        "top_tracks": ["Old before"],
                    }
                ),
                json.dumps(
                    {
                        "date": "2026-05-02",
                        "track_count": 9,
                        "minutes_played": 99.0,
                        "unique_artists": 1,
                        "unique_tracks": 1,
                        "top_artists": ["old-window"],
                        "top_tracks": ["Old window"],
                    }
                ),
                json.dumps(
                    {
                        "date": "2026-05-03",
                        "track_count": 1,
                        "minutes_played": 1.0,
                        "unique_artists": 1,
                        "unique_tracks": 1,
                        "top_artists": ["C"],
                        "top_tracks": ["Old after"],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "covered_dates": ["2026-05-01", "2026-05-02", "2026-05-03"],
                "first_date": "2026-05-01",
                "last_date": "2026-05-03",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = materialize_spotify_daily(
        output=output,
        start=date(2026, 5, 2),
        end=date(2026, 5, 3),
    )
    rows = list(iter_spotify_daily_signals(output))

    assert calls == [(date(2026, 5, 2), date(2026, 5, 3))]
    assert [row.date for row in rows] == [date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3)]
    assert [row.track_count for row in rows] == [1, 1, 1]
    assert rows[1].top_artists == ("B",)
    assert rows[1].minutes_played == 3.0
    assert manifest["covered_dates"] == ["2026-05-01", "2026-05-02", "2026-05-03"]
    assert manifest["window_start"] == "2026-05-02"
    assert manifest["window_end"] == "2026-05-03"


def test_materialize_personal_daily_signals_records_used_product_high_water(monkeypatch, tmp_path):
    upstream = tmp_path / "activity_content_daily.ndjson"
    upstream.write_text("{}\n", encoding="utf-8")
    rows = [("activity_content", datetime(2026, 5, 1, tzinfo=timezone.utc).date(), "focused_minutes", 42.0, {})]

    monkeypatch.setattr(
        "lynchpin.ingest.personal_signals_materialize._personal_daily_signal_rows_with_inputs",
        lambda: (rows, (upstream,)),
    )

    output = tmp_path / "personal_daily_signals.ndjson"
    manifest = materialize_personal_daily_signals(output=output)

    assert manifest["row_count"] == 1
    assert manifest["source_counts"] == {"activity_content": 1}
    assert manifest["schema_version"] == PERSONAL_DAILY_SIGNALS_SCHEMA_VERSION
    assert manifest["input_file_count"] == 1
    assert manifest["input_latest_mtime"] is not None
    assert manifest["covered_dates"] == ["2026-05-01"]


def test_materialize_personal_daily_signals_merges_window_and_tracks_precise_coverage(monkeypatch, tmp_path):
    output = tmp_path / "personal_daily_signals.ndjson"
    output.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "source": "activity_content",
                        "date": "2026-05-01",
                        "metric": "focused_minutes",
                        "value": 10,
                        "dimensions": {},
                    }
                ),
                json.dumps(
                    {
                        "source": "activity_content",
                        "date": "2026-05-02",
                        "metric": "focused_minutes",
                        "value": 20,
                        "dimensions": {},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "covered_dates": ["2026-05-01", "2026-05-02"],
                "first_date": "2026-05-01",
                "last_date": "2026-05-02",
                "input_file_count": 0,
            }
        ),
        encoding="utf-8",
    )
    replacement = [("keylog", date(2026, 5, 2), "keypress_count", 42.0, {})]
    monkeypatch.setattr(
        "lynchpin.ingest.personal_signals_materialize._window_personal_daily_signal_rows_with_inputs",
        lambda start, end: (replacement, ()),
    )

    manifest = materialize_personal_daily_signals(
        output=output,
        start=date(2026, 5, 2),
        end=date(2026, 5, 4),
    )
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    assert [(row["source"], row["date"], row["value"]) for row in rows] == [
        ("activity_content", "2026-05-01", 10.0),
        ("keylog", "2026-05-02", 42.0),
    ]
    assert manifest["covered_dates"] == ["2026-05-01", "2026-05-02", "2026-05-03"]
    assert manifest["window_semantics"] == "start inclusive, end exclusive"


def test_personal_daily_signal_inputs_fall_back_to_raw_roots(tmp_path) -> None:
    from lynchpin.ingest.personal_signals_materialize import _personal_daily_signal_input_files
    from lynchpin.materialization import MaterializedDataset

    keylog_root = tmp_path / "keylog" / "logs"
    keylog_root.mkdir(parents=True)

    inputs = _personal_daily_signal_input_files(
        {
            "keylog": MaterializedDataset(
                name="keylog",
                status="ready",
                authority="fixture",
                query_surface="fixture",
                materialized_paths=(),
                raw_roots=(keylog_root,),
                row_count=1,
                first_date=date(2026, 5, 1),
                last_date=date(2026, 5, 1),
                materialization_hint="fixture",
                reason="fixture",
            )
        },
        {"keylog"},
    )

    assert inputs == (keylog_root,)


def test_personal_daily_signal_inputs_prefer_materialized_paths(tmp_path) -> None:
    from lynchpin.ingest.personal_signals_materialize import _personal_daily_signal_input_files
    from lynchpin.materialization import MaterializedDataset

    raw_root = tmp_path / "keylog" / "logs"
    raw_root.mkdir(parents=True)
    product = tmp_path / "activity_content_daily.ndjson"
    product.write_text("{}\n", encoding="utf-8")

    inputs = _personal_daily_signal_input_files(
        {
            "activity_content": MaterializedDataset(
                name="activity_content",
                status="ready",
                authority="fixture",
                query_surface="fixture",
                materialized_paths=(product,),
                raw_roots=(raw_root,),
                row_count=1,
                first_date=date(2026, 5, 1),
                last_date=date(2026, 5, 1),
                materialization_hint="fixture",
                reason="fixture",
            )
        },
        {"activity_content"},
    )

    assert inputs == (product,)


def test_window_personal_daily_signal_inputs_include_overlapping_zero_row_sources(tmp_path, monkeypatch) -> None:
    from lynchpin.ingest.personal_signals_materialize import _window_personal_daily_signal_rows_with_inputs
    from lynchpin.materialization import MaterializedDataset

    product = tmp_path / "keylog_daily.ndjson"
    product.write_text("", encoding="utf-8")

    monkeypatch.setattr("lynchpin.sources.keylog.daily_activity", lambda *, start, end: [])
    monkeypatch.setattr(
        "lynchpin.materialization.audit_materialization",
        lambda: [
            MaterializedDataset(
                name="keylog",
                status="ready",
                authority="fixture",
                query_surface="fixture",
                materialized_paths=(product,),
                raw_roots=(),
                row_count=0,
                first_date=date(2026, 5, 1),
                last_date=date(2026, 5, 1),
                materialization_hint="fixture",
                reason="fixture",
            )
        ],
        raising=False,
    )

    rows, inputs = _window_personal_daily_signal_rows_with_inputs(
        date(2026, 5, 1),
        date(2026, 5, 2),
    )

    assert rows == []
    assert inputs == (product,)


def test_personal_daily_signal_rows_include_keylog_metrics(monkeypatch) -> None:
    from dataclasses import dataclass

    from lynchpin.materialization import MaterializedDataset
    from lynchpin.ingest.personal_signals_materialize import _window_personal_daily_signal_rows

    @dataclass
    class KeylogRow:
        date: date
        event_count: int
        keypress_count: int
        changed_keypress_count: int
        session_count: int
        first_ts: datetime | None = None
        last_ts: datetime | None = None

    monkeypatch.setattr(
        "lynchpin.sources.keylog.daily_activity",
        lambda *, start, end: [
            KeylogRow(
                date=date(2026, 5, 1),
                event_count=7,
                keypress_count=5,
                changed_keypress_count=4,
                session_count=2,
            )
        ],
    )

    rows = _window_personal_daily_signal_rows(
        date(2026, 5, 1),
        date(2026, 5, 2),
        {
            "keylog": MaterializedDataset(
                name="keylog",
                status="ready",
                authority="fixture",
                query_surface="fixture",
                materialized_paths=(),
                raw_roots=(),
                row_count=1,
                first_date=date(2026, 5, 1),
                last_date=date(2026, 5, 1),
                materialization_hint="fixture",
                reason="fixture",
            )
        },
    )

    assert rows == [
        ("keylog", date(2026, 5, 1), "keypress_count", 5.0, {}),
        ("keylog", date(2026, 5, 1), "changed_keypress_count", 4.0, {}),
        ("keylog", date(2026, 5, 1), "event_count", 7.0, {}),
        ("keylog", date(2026, 5, 1), "session_count", 2.0, {}),
    ]


def test_personal_daily_signal_rows_include_rich_physiology_metrics(monkeypatch) -> None:
    from lynchpin.materialization import MaterializedDataset
    from lynchpin.ingest.personal_signals_materialize import _window_personal_daily_signal_rows

    health = SimpleNamespace(
        date=date(2026, 5, 1),
        steps=9000,
        stress_avg=42.0,
        stress_count=4,
        heart_rate_avg=71.0,
        heart_rate_resting=62.0,
        hrv_rmssd_avg=38.5,
        hrv_count=3,
        spo2_avg=97.0,
        spo2_count=2,
        vitality_score=88.0,
        calories=2400.0,
    )
    sleep_entry = SimpleNamespace(
        date=date(2026, 5, 1),
        total_minutes=420.0,
        avg_score=82.0,
        quality_label="good",
    )
    sleep_arch = SimpleNamespace(
        date=date(2026, 5, 1),
        sleep_id="sleep-1",
        total_min=430.0,
        awake_min=10.0,
        light_min=250.0,
        deep_min=80.0,
        rem_min=90.0,
        deep_pct=18.6,
        rem_pct=20.9,
        stage_transitions=7,
        first_rem_min=95.0,
    )

    monkeypatch.setattr("lynchpin.sources.health.daily_health_summary", lambda *, start, end: [health])
    monkeypatch.setattr("lynchpin.sources.sleep.entries_in_range", lambda *, start, end: [sleep_entry])
    monkeypatch.setattr("lynchpin.sources.sleep.sleep_architecture", lambda *, start, end: [sleep_arch])

    rows = _window_personal_daily_signal_rows(
        date(2026, 5, 1),
        date(2026, 5, 2),
        {
            "health": MaterializedDataset(
                name="health",
                status="ready",
                authority="fixture",
                query_surface="fixture",
                materialized_paths=(),
                raw_roots=(),
                row_count=1,
                first_date=date(2026, 5, 1),
                last_date=date(2026, 5, 1),
                materialization_hint="fixture",
                reason="fixture",
            ),
            "sleep": MaterializedDataset(
                name="sleep",
                status="ready",
                authority="fixture",
                query_surface="fixture",
                materialized_paths=(),
                raw_roots=(),
                row_count=1,
                first_date=date(2026, 5, 1),
                last_date=date(2026, 5, 1),
                materialization_hint="fixture",
                reason="fixture",
            ),
        },
    )

    assert ("health", date(2026, 5, 1), "stress_avg", 42.0, {"count": 4}) in rows
    assert ("health", date(2026, 5, 1), "resting_heart_rate", 62.0, {}) in rows
    assert ("health", date(2026, 5, 1), "hrv_rmssd", 38.5, {"count": 3}) in rows
    assert ("health", date(2026, 5, 1), "spo2_avg", 97.0, {"count": 2}) in rows
    assert ("health", date(2026, 5, 1), "vitality_score", 88.0, {"calories": 2400.0}) in rows
    assert ("sleep", date(2026, 5, 1), "sleep_score", 82.0, {}) in rows
    assert ("sleep", date(2026, 5, 1), "sleep_deep_pct", 18.6, {"sleep_id": "sleep-1"}) in rows
    assert ("sleep", date(2026, 5, 1), "sleep_stage_transitions", 7.0, {"sleep_id": "sleep-1"}) in rows
    assert ("sleep", date(2026, 5, 1), "sleep_first_rem_minutes", 95.0, {"sleep_id": "sleep-1"}) in rows


def test_personal_daily_signal_rows_translate_half_open_window_for_inclusive_readers(monkeypatch) -> None:
    from lynchpin.materialization import MaterializedDataset
    from lynchpin.ingest.personal_signals_materialize import _window_personal_daily_signal_rows

    calls: list[tuple[str, date, date, bool | None]] = []

    def ready_source(name: str) -> MaterializedDataset:
        return MaterializedDataset(
            name=name,
            status="ready",
            authority="fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2026, 5, 1),
            last_date=date(2026, 5, 1),
            materialization_hint="fixture",
            reason="fixture",
        )

    def empty_daily(source: str):
        def inner(*, start, end, ensure=None):
            calls.append((source, start, end, ensure))
            return []

        return inner

    monkeypatch.setattr("lynchpin.sources.web.daily_browsing", empty_daily("webhistory"))
    monkeypatch.setattr("lynchpin.sources.health.daily_health_summary", empty_daily("health"))
    monkeypatch.setattr("lynchpin.sources.keylog.daily_activity", empty_daily("keylog"))
    monkeypatch.setattr("lynchpin.sources.sleep.entries_in_range", empty_daily("sleep"))
    monkeypatch.setattr("lynchpin.sources.sleep.sleep_architecture", empty_daily("sleep_architecture"))
    monkeypatch.setattr("lynchpin.sources.substance.daily_summary", empty_daily("substance"))

    rows = _window_personal_daily_signal_rows(
        date(2026, 5, 1),
        date(2026, 5, 2),
        {
            "webhistory": ready_source("webhistory"),
            "health": ready_source("health"),
            "keylog": ready_source("keylog"),
            "sleep": ready_source("sleep"),
            "substance": ready_source("substance"),
        },
    )

    assert rows == []
    assert calls == [
        ("webhistory", date(2026, 5, 1), date(2026, 5, 1), False),
        ("health", date(2026, 5, 1), date(2026, 5, 1), None),
        ("keylog", date(2026, 5, 1), date(2026, 5, 1), None),
        ("sleep", date(2026, 5, 1), date(2026, 5, 1), None),
        ("sleep_architecture", date(2026, 5, 1), date(2026, 5, 1), None),
        ("substance", date(2026, 5, 1), date(2026, 5, 1), None),
    ]


def test_personal_daily_signal_rows_include_wykop_metrics(monkeypatch) -> None:
    from dataclasses import dataclass

    from lynchpin.materialization import MaterializedDataset
    from lynchpin.ingest.personal_signals_materialize import _window_personal_daily_signal_rows

    @dataclass
    class WykopRow:
        date: str
        comments: int
        own_chars: int
        total_chars: int
        upvotes: int
        downvotes: int
        comment_ids: tuple[int, ...] = ()

    calls = []

    def fake_daily_activity(*, start, end):
        calls.append((start, end))
        return [
            WykopRow(
                date="2026-05-01",
                comments=2,
                own_chars=30,
                total_chars=45,
                upvotes=3,
                downvotes=1,
            )
        ]

    monkeypatch.setattr("lynchpin.sources.wykop.daily_activity", fake_daily_activity)

    rows = _window_personal_daily_signal_rows(
        date(2026, 5, 1),
        date(2026, 5, 2),
        {
            "wykop": MaterializedDataset(
                name="wykop",
                status="ready",
                authority="fixture",
                query_surface="fixture",
                materialized_paths=(),
                raw_roots=(),
                row_count=1,
                first_date=date(2026, 5, 1),
                last_date=date(2026, 5, 1),
                materialization_hint="fixture",
                reason="fixture",
            )
        },
    )

    assert calls == [("2026-05-01", "2026-05-01")]
    assert rows == [
        ("wykop", date(2026, 5, 1), "comment_count", 2.0, {}),
        ("wykop", date(2026, 5, 1), "own_chars", 30.0, {}),
        ("wykop", date(2026, 5, 1), "total_chars", 45.0, {}),
        ("wykop", date(2026, 5, 1), "upvote_count", 3.0, {}),
        ("wykop", date(2026, 5, 1), "downvote_count", 1.0, {}),
    ]


def test_daily_signal_contract_sources_have_materializer_branches() -> None:
    from lynchpin.core.source_contracts import DAILY_SIGNAL_SOURCE_NAMES
    from lynchpin.ingest.personal_signals_materialize import _window_personal_daily_signal_rows

    source = inspect.getsource(_window_personal_daily_signal_rows)
    materialized_sources = set(re.findall(r'_overlaps\(audit_by_name, "([^"]+)"', source))

    assert set(DAILY_SIGNAL_SOURCE_NAMES) <= materialized_sources
